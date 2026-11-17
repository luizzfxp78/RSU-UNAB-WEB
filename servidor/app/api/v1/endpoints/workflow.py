from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.api.deps import get_current_user, require_admin
from app.api.v1.endpoints.project_lifecycle import _add_business_days
from app.core.config import get_settings
from app.db.mysql import fetch_all, fetch_one, get_connection
from app.schemas.auth import AuthUser
from app.schemas.workflow import ConvocatoriaIn, EvaluacionProyectoIn, ProyectoFormularioIn
from app.services.project_pdf import generate_annexes_pdf
from app.services.module_dashboard import build_module_payload
from app.services.general_dashboard import build_general_dashboard
from app.services.reports_dashboard import build_reports_dashboard, build_reports_excel
from app.services.evaluations_dashboard import build_evaluations_dashboard
from app.services.convocations_dashboard import build_convocations_dashboard
from app.services.websocket_manager import ws_manager
from app.services.notification_service import eligible_call_user_ids, notify_admins, notify_users

router = APIRouter(prefix="/flujo-rsu", tags=["Convocatorias y presentación RSU"])


def _state_id(entity: str, name: str) -> str:
    row = fetch_one(
        "SELECT catalogo_estado_id FROM catalogo_estado WHERE entidad=%s AND LOWER(nombre)=LOWER(%s) LIMIT 1",
        (entity, name),
    )
    if row is None:
        raise HTTPException(status_code=400, detail=f"No existe el estado {entity}:{name} en catalogo_estado.")
    return row["catalogo_estado_id"]


def _role_id(name: str) -> str:
    row = fetch_one("SELECT rol_id FROM catalogo_rol WHERE LOWER(nombre)=LOWER(%s) AND activo=1 LIMIT 1", (name,))
    if row is None:
        raise HTTPException(status_code=400, detail=f"No existe el rol de proyecto '{name}'.")
    return row["rol_id"]


def _is_teacher_or_student(user: AuthUser) -> bool:
    return user.tipo_usuario.strip().lower() in {"docente", "estudiante"}


def _project_type_is_rsu(type_name: str) -> bool:
    return "responsabilidad social universitaria" in type_name.lower()


def _assert_project_access(project_id: str, user: AuthUser, admin_allowed: bool = True) -> dict:
    project = fetch_one(
        """
        SELECT p.*, ce.nombre AS estado, cv.tipo_proyecto_id, tp.nombre AS tipo_proyecto,
               pa.nombre AS periodo_academico, la.nombre AS linea_accion, la.codigo AS linea_codigo
        FROM proyecto p
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=p.periodo_academico_id
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        WHERE p.proyecto_id=%s
        """,
        (project_id,),
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    role = str(user.rol_sistema or "").strip().lower()
    is_admin = role in {"administrador_rsu", "director_rsu"} if role else user.tipo_usuario.strip().lower() in {"administrativo", "administrador", "admin"}
    responsible = fetch_one(
        """
        SELECT 1
        FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        WHERE rp.proyecto_id=%s AND rp.persona_id=%s AND LOWER(cr.nombre)='responsable' AND rp.activo=1
        LIMIT 1
        """,
        (project_id, user.persona_id),
    )
    if responsible is None and not (admin_allowed and is_admin):
        raise HTTPException(status_code=403, detail="No tienes permiso para acceder a este proyecto.")
    return project


def _serialize_project(project_id: str, user: AuthUser) -> dict:
    project = _assert_project_access(project_id, user)
    form = fetch_one("SELECT * FROM proyecto_formulario WHERE proyecto_id=%s", (project_id,)) or {}
    participants = fetch_all(
        """
        SELECT rp.persona_id, CONCAT(pe.nombres,' ',pe.apellidos) AS nombre_completo, pe.dni,
               cr.nombre AS rol
        FROM rol_proyecto rp
        INNER JOIN persona pe ON pe.persona_id=rp.persona_id
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        WHERE rp.proyecto_id=%s AND rp.activo=1
        ORDER BY CASE WHEN LOWER(cr.nombre)='responsable' THEN 0 ELSE 1 END, pe.apellidos, pe.nombres
        """,
        (project_id,),
    )
    evaluations = fetch_all(
        """
        SELECT ep.*, CONCAT(p.nombres,' ',p.apellidos) AS evaluador
        FROM evaluacion_proyecto ep
        INNER JOIN persona p ON p.persona_id=ep.evaluador_persona_id
        WHERE ep.proyecto_id=%s
        ORDER BY ep.fecha_evaluacion DESC
        """,
        (project_id,),
    )
    budget = fetch_all(
        """
        SELECT financiamiento_id, partida, descripcion AS bien_servicio, unidad_medida,
               cantidad, costo_unitario, monto_total, aprobado
        FROM financiamiento WHERE proyecto_id=%s ORDER BY financiamiento_id
        """,
        (project_id,),
    )
    project["problematica"] = form.get("problematica")
    project["objetivo"] = form.get("objetivo")
    project["metodologia"] = form.get("metodologia")
    project["impacto_social"] = form.get("impacto_social")
    project["impacto_estudiantes"] = form.get("impacto_estudiantes")
    project["solicitud_tipo"] = form.get("solicitud_tipo")
    project["domicilio_solicitante"] = form.get("domicilio_solicitante")
    project["condicion_solicitante"] = form.get("condicion_solicitante")
    project["revision_actual"] = form.get("revision_actual", 1)
    try:
        project["cronograma"] = json.loads(form.get("cronograma_json") or "[]")
    except json.JSONDecodeError:
        project["cronograma"] = []
    project["participantes"] = participants
    project["colaboradores"] = [row for row in participants if str(row["rol"]).lower() == "colaborador"]
    project["responsable"] = next((row for row in participants if str(row["rol"]).lower() == "responsable"), None)
    project["presupuesto"] = budget
    project["evaluaciones"] = evaluations
    project["ultima_observacion"] = evaluations[0]["observaciones"] if evaluations and evaluations[0]["resultado"] == "con_observaciones" else None
    return project




def _is_admin_user(user: AuthUser) -> bool:
    role = str(user.rol_sistema or "").strip().lower()
    if role:
        return role in {"administrador_rsu", "director_rsu"}
    return user.tipo_usuario.strip().lower() in {"administrativo", "administrador", "admin"}


@router.get("/modulos/{module_key}")
def module_dashboard(
    module_key: str,
    periodo_academico_id: str | None = Query(default=None),
    user: AuthUser = Depends(get_current_user),
):
    """Datos reales para las pantallas visuales del cliente de escritorio.

    El panel de inicio admite un filtro institucional por ciclo académico. El
    resto de módulos conserva su contrato anterior y sus filtros locales.
    """
    key = module_key.strip().lower()
    if key == "inicio":
        return build_general_dashboard(user, periodo_academico_id)
    if key == "reportes":
        return build_reports_dashboard(user, faculty=None, period_id=periodo_academico_id)
    if key == "evaluaciones":
        return build_evaluations_dashboard(user, module=None, faculty=None, period_id=periodo_academico_id)
    if key == "convocatorias":
        return build_convocations_dashboard(user, call_type=None, faculty=None, period_id=periodo_academico_id)
    return build_module_payload(module_key, user)


@router.get("/convocatorias/dashboard")
def convocations_dashboard(
    tipo: str | None = Query(default=None),
    facultad: str | None = Query(default=None),
    periodo_academico_id: str | None = Query(default=None),
    user: AuthUser = Depends(get_current_user),
):
    """Dashboard de convocatorias con alcance institucional o personalizado según el perfil."""
    return build_convocations_dashboard(
        user,
        call_type=tipo,
        faculty=facultad,
        period_id=periodo_academico_id,
    )


@router.get("/evaluaciones/dashboard")
def evaluations_dashboard(
    modulo: str | None = Query(default=None),
    facultad: str | None = Query(default=None),
    periodo_academico_id: str | None = Query(default=None),
    user: AuthUser = Depends(get_current_user),
):
    """Dashboard analítico de evaluaciones con filtros institucionales."""
    return build_evaluations_dashboard(
        user,
        module=modulo,
        faculty=facultad,
        period_id=periodo_academico_id,
    )


@router.get("/reportes/dashboard")
def reports_dashboard(
    facultad: str | None = Query(default=None),
    periodo_academico_id: str | None = Query(default=None),
    user: AuthUser = Depends(get_current_user),
):
    """Dashboard analítico de reportes con filtros por facultad y ciclo."""
    return build_reports_dashboard(user, faculty=facultad, period_id=periodo_academico_id)


@router.get("/reportes/exportar-excel")
def export_reports_excel(
    facultad: str | None = Query(default=None),
    periodo_academico_id: str | None = Query(default=None),
    user: AuthUser = Depends(get_current_user),
):
    """Exporta el reporte institucional filtrado en formato XLSX."""
    content, filename = build_reports_excel(user, faculty=facultad, period_id=periodo_academico_id)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/dashboard")
def dashboard_summary(user: AuthUser = Depends(get_current_user)):
    """Devuelve los datos reales usados por el panel principal.

    La respuesta se adapta al tipo de usuario actual. Los administrativos ven
    indicadores institucionales y la bandeja pendiente de evaluación; docentes
    y estudiantes ven únicamente los proyectos donde figuran como responsables.
    """
    is_admin = _is_admin_user(user)

    active_calls_row = fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM convocatoria cv
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        WHERE LOWER(ce.nombre)='abierta'
          AND cv.fecha_inicio <= %s
          AND cv.fecha_limite_presentacion >= %s
        """,
        (date.today(), date.today()),
    ) or {"total": 0}

    if is_admin:
        project_filter = ""
        project_params: tuple[object, ...] = ()
    else:
        project_filter = """
          INNER JOIN rol_proyecto rp_dashboard ON rp_dashboard.proyecto_id=p.proyecto_id
          INNER JOIN catalogo_rol cr_dashboard ON cr_dashboard.rol_id=rp_dashboard.rol_id
          WHERE rp_dashboard.persona_id=%s
            AND rp_dashboard.activo=1
            AND LOWER(cr_dashboard.nombre)='responsable'
        """
        project_params = (user.persona_id,)

    project_metrics = fetch_one(
        f"""
        SELECT
            COUNT(DISTINCT p.proyecto_id) AS total_proyectos,
            COUNT(DISTINCT CASE WHEN LOWER(ce.nombre) IN ('presentado','en_evaluacion') THEN p.proyecto_id END) AS en_evaluacion,
            COUNT(DISTINCT CASE WHEN LOWER(ce.nombre)='con_observaciones' THEN p.proyecto_id END) AS observados,
            COUNT(DISTINCT CASE WHEN LOWER(ce.nombre)='aprobado' THEN p.proyecto_id END) AS aprobados
        FROM proyecto p
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {project_filter}
        """,
        project_params,
    ) or {}

    status_rows = fetch_all(
        f"""
        SELECT ce.nombre AS estado, COUNT(DISTINCT p.proyecto_id) AS cantidad
        FROM proyecto p
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {project_filter}
        GROUP BY ce.catalogo_estado_id, ce.nombre
        ORDER BY cantidad DESC, ce.nombre
        """,
        project_params,
    )

    calls = fetch_all(
        """
        SELECT cv.convocatoria_id, tp.nombre AS tipo_proyecto,
               pa.nombre AS periodo_academico, cv.fecha_inicio,
               cv.fecha_limite_presentacion,
               CASE
                   WHEN LOWER(ce.nombre)='abierta' AND cv.fecha_inicio <= %s AND cv.fecha_limite_presentacion >= %s THEN 'Abierta'
                   WHEN cv.fecha_inicio > %s THEN 'Próxima'
                   WHEN cv.fecha_limite_presentacion < %s THEN 'Cerrada'
                   ELSE ce.nombre
               END AS estado
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        ORDER BY
            CASE
                WHEN LOWER(ce.nombre)='abierta' AND cv.fecha_inicio <= %s AND cv.fecha_limite_presentacion >= %s THEN 0
                WHEN cv.fecha_inicio > %s THEN 1
                ELSE 2
            END,
            cv.fecha_inicio DESC
        LIMIT 6
        """,
        (
            date.today(), date.today(), date.today(), date.today(),
            date.today(), date.today(), date.today(),
        ),
    )

    if not is_admin:
        calls = [row for row in calls if str(row.get("estado") or "").strip().lower() == "abierta"]

    if is_admin:
        review_queue = fetch_all(
            """
            SELECT p.proyecto_id, p.titulo, p.fecha_presentacion,
                   ce.nombre AS estado, tp.nombre AS tipo_proyecto,
                   CONCAT(pe.nombres,' ',pe.apellidos) AS responsable,
                   pf.revision_actual
            FROM proyecto p
            INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
            INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
            INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
            LEFT JOIN proyecto_formulario pf ON pf.proyecto_id=p.proyecto_id
            INNER JOIN rol_proyecto rp ON rp.proyecto_id=p.proyecto_id AND rp.activo=1
            INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id AND LOWER(cr.nombre)='responsable'
            INNER JOIN persona pe ON pe.persona_id=rp.persona_id
            WHERE LOWER(ce.nombre) IN ('presentado','en_evaluacion')
            ORDER BY p.fecha_presentacion DESC, p.fecha_creacion DESC
            LIMIT 8
            """
        )
    else:
        review_queue = fetch_all(
            """
            SELECT p.proyecto_id, p.titulo, p.fecha_presentacion,
                   ce.nombre AS estado, tp.nombre AS tipo_proyecto,
                   CONCAT(pe.nombres,' ',pe.apellidos) AS responsable,
                   pf.revision_actual
            FROM proyecto p
            INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
            INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
            INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
            LEFT JOIN proyecto_formulario pf ON pf.proyecto_id=p.proyecto_id
            INNER JOIN rol_proyecto rp ON rp.proyecto_id=p.proyecto_id AND rp.activo=1
            INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id AND LOWER(cr.nombre)='responsable'
            INNER JOIN persona pe ON pe.persona_id=rp.persona_id
            WHERE rp.persona_id=%s
            ORDER BY p.fecha_creacion DESC
            LIMIT 8
            """,
            (user.persona_id,),
        )

    return {
        "permissions": {
            "is_admin": is_admin,
            "can_manage_calls": is_admin,
            "can_register_people": is_admin,
            "can_manage_personnel": is_admin,
            "can_evaluate": is_admin,
            "can_submit_project": _is_teacher_or_student(user),
        },
        "metrics": {
            "convocatorias_activas": int(active_calls_row.get("total") or 0),
            "proyectos_en_evaluacion": int(project_metrics.get("en_evaluacion") or 0),
            "pendientes_subsanacion": int(project_metrics.get("observados") or 0),
            "proyectos_aprobados": int(project_metrics.get("aprobados") or 0),
            "total_proyectos": int(project_metrics.get("total_proyectos") or 0),
        },
        "convocatorias": calls,
        "proyectos_por_estado": status_rows,
        "bandeja_revision": review_queue,
        "notifications_available": False,
    }


@router.get("/convocatorias")
def list_calls(_: AuthUser = Depends(get_current_user)):
    return fetch_all(
        """
        SELECT cv.*, tp.nombre AS tipo_proyecto, tp.horas_minimas, pa.nombre AS periodo_academico,
               ce.nombre AS estado
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        ORDER BY cv.fecha_inicio DESC, tp.nombre
        """
    )


@router.get("/convocatorias/activas")
def active_calls(tipo: str | None = None, _: AuthUser = Depends(get_current_user)):
    params: list[object] = [date.today(), date.today()]
    extra = ""
    if tipo:
        extra = " AND LOWER(tp.nombre) LIKE LOWER(%s)"
        params.append(f"%{tipo.strip()}%")
    return fetch_all(
        f"""
        SELECT cv.*, tp.nombre AS tipo_proyecto, tp.horas_minimas, pa.nombre AS periodo_academico,
               ce.nombre AS estado
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        WHERE LOWER(ce.nombre)='abierta'
          AND cv.fecha_inicio <= %s AND cv.fecha_limite_presentacion >= %s
          {extra}
        ORDER BY cv.fecha_limite_presentacion, tp.nombre
        """,
        tuple(params),
    )


@router.post("/convocatorias")
async def create_call(data: ConvocatoriaIn, admin: AuthUser = Depends(require_admin)):
    call_id = str(uuid4())
    closed_state = _state_id("convocatoria", "cerrada")
    with get_connection() as conn:
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO convocatoria
                (convocatoria_id, tipo_proyecto_id, periodo_academico_id, fecha_inicio,
                 fecha_limite_presentacion, monto_maximo_financiamiento, bases_url, estado_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    call_id,
                    data.tipo_proyecto_id,
                    data.periodo_academico_id,
                    data.fecha_inicio,
                    data.fecha_limite_presentacion,
                    data.monto_maximo_financiamiento,
                    data.bases_url,
                    closed_state,
                ),
            )
            conn.commit()
            cur.close()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    created = fetch_one(
        """
        SELECT cv.*, tp.nombre AS tipo_proyecto, pa.nombre AS periodo_academico
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id
        WHERE cv.convocatoria_id=%s
        """,
        (call_id,),
    ) or {}
    await notify_admins(
        notification_type="convocatoria_programada",
        title="Convocatoria programada",
        message=(
            f"La convocatoria {created.get('tipo_proyecto', 'RSU')} - {created.get('periodo_academico', '')} "
            f"fue programada para iniciar el {data.fecha_inicio}."
        ),
        module="Módulo de Convocatorias",
        priority="informativa",
        entity_type="convocatoria",
        entity_id=call_id,
        data={
            "convocatoria_id": call_id,
            "fecha_inicio": data.fecha_inicio,
            "fecha_limite_presentacion": data.fecha_limite_presentacion,
            "pantalla": "convocatorias",
        },
        event_key=f"convocatoria:{call_id}:programada",
        generated_by=admin.nombre_completo,
    )
    return created


@router.put("/convocatorias/{call_id}")
def update_call(call_id: str, data: ConvocatoriaIn, _: AuthUser = Depends(require_admin)):
    with get_connection() as conn:
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE convocatoria SET tipo_proyecto_id=%s, periodo_academico_id=%s,
                  fecha_inicio=%s, fecha_limite_presentacion=%s,
                  monto_maximo_financiamiento=%s, bases_url=%s
                WHERE convocatoria_id=%s
                """,
                (
                    data.tipo_proyecto_id,
                    data.periodo_academico_id,
                    data.fecha_inicio,
                    data.fecha_limite_presentacion,
                    data.monto_maximo_financiamiento,
                    data.bases_url,
                    call_id,
                ),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Convocatoria no encontrada.")
            conn.commit()
            cur.close()
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return fetch_one("SELECT * FROM convocatoria WHERE convocatoria_id=%s", (call_id,))


@router.post("/convocatorias/{call_id}/activar")
async def activate_call(call_id: str, admin: AuthUser = Depends(require_admin)):
    row = fetch_one(
        """
        SELECT cv.*, tp.nombre AS tipo_proyecto, pa.nombre AS periodo_academico
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id
        WHERE cv.convocatoria_id=%s
        """,
        (call_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Convocatoria no encontrada.")
    # La convocatoria de proyectos RSU permanece habilitada durante dos semanas hábiles
    # (10 días laborables). El cierre real se calcula al momento de activarla.
    start_date = date.today()
    deadline_date = _add_business_days(start_date, 10)
    open_state = _state_id("convocatoria", "abierta")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE convocatoria SET estado_id=%s, fecha_inicio=%s, fecha_limite_presentacion=%s WHERE convocatoria_id=%s",
            (open_state, start_date, deadline_date, call_id),
        )
        conn.commit()
        cur.close()
    await ws_manager.broadcast(
        {
            "type": "convocatoria.activada",
            "audience": ["docente", "estudiante"],
            "message": f"Convocatoria disponible: {row['tipo_proyecto']} - {row['periodo_academico']}.",
            "data": {"convocatoria_id": call_id, "tipo_proyecto": row["tipo_proyecto"], "periodo": row["periodo_academico"], "activada_por": admin.nombre_completo},
        }
    )
    await notify_users(
        eligible_call_user_ids(),
        notification_type="convocatoria_publicada",
        title="Nueva convocatoria RSU",
        message=(
            f"Se habilitó la convocatoria {row['tipo_proyecto']} - {row['periodo_academico']}. "
            f"Puedes presentar tu proyecto hasta el {deadline_date}."
        ),
        module="Módulo de Convocatorias",
        priority="informativa",
        entity_type="convocatoria",
        entity_id=call_id,
        data={
            "convocatoria_id": call_id,
            "fecha_inicio": start_date,
            "fecha_limite_presentacion": deadline_date,
            "pantalla": "convocatorias",
        },
        event_key=f"convocatoria:{call_id}:activada:{start_date.isoformat()}",
        generated_by=admin.nombre_completo,
    )
    return {"ok": True, "message": f"Convocatoria activada por dos semanas hábiles, hasta {deadline_date}."}


@router.post("/convocatorias/{call_id}/cerrar")
async def close_call(call_id: str, _: AuthUser = Depends(require_admin)):
    closed_state = _state_id("convocatoria", "cerrada")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE convocatoria SET estado_id=%s WHERE convocatoria_id=%s", (closed_state, call_id))
        if cur.rowcount == 0:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Convocatoria no encontrada.")
        conn.commit()
        cur.close()
    await ws_manager.broadcast({"type": "convocatoria.cerrada", "message": "Una convocatoria RSU fue cerrada.", "data": {"convocatoria_id": call_id}})
    await notify_users(
        eligible_call_user_ids(),
        notification_type="convocatoria_cerrada",
        title="Convocatoria cerrada",
        message="La convocatoria RSU ya no admite nuevas presentaciones.",
        module="Módulo de Convocatorias",
        priority="informativa",
        entity_type="convocatoria",
        entity_id=call_id,
        data={"convocatoria_id": call_id, "pantalla": "convocatorias"},
        event_key=f"convocatoria:{call_id}:cerrada",
    )
    return {"ok": True, "message": "Convocatoria cerrada."}


@router.get("/personas-seleccion")
def people_for_selection(q: str | None = None, user: AuthUser = Depends(get_current_user)):
    params: list[object] = [user.persona_id]
    filter_sql = ""
    if q and q.strip():
        like = f"%{q.strip()}%"
        filter_sql = " AND (p.nombres LIKE %s OR p.apellidos LIKE %s OR p.dni LIKE %s)"
        params.extend([like, like, like])
    return fetch_all(
        f"""
        SELECT p.persona_id, CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo, p.dni,
               CASE WHEN d.persona_id IS NOT NULL THEN 'Docente'
                    WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                    WHEN af.tipo_vinculo='no_docente' THEN 'No docente'
                    WHEN af.tipo_vinculo='egresado' THEN 'Egresado'
                    WHEN af.tipo_vinculo='graduado' THEN 'Graduado'
                    WHEN a.persona_id IS NOT NULL THEN 'Administrativo'
                    ELSE 'Persona' END AS tipo_persona,
               CASE WHEN u.usuario_id IS NOT NULL THEN 1 ELSE 0 END AS tiene_cuenta,
               CASE WHEN u.usuario_id IS NOT NULL AND u.activo=1 THEN 1 ELSE 0 END AS cuenta_activa
        FROM persona p
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        LEFT JOIN usuario u ON u.persona_id=p.persona_id
        WHERE p.activo=1 AND p.persona_id<>%s {filter_sql}
        ORDER BY p.apellidos, p.nombres LIMIT 300
        """,
        tuple(params),
    )


@router.post("/proyectos")
async def submit_project(data: ProyectoFormularioIn, user: AuthUser = Depends(get_current_user)):
    raise HTTPException(
        status_code=409,
        detail="Este formulario fue reemplazado por el ciclo institucional de Anexos 1 y 2. Usa /ciclo-rsu/proyectos.",
    )
    if not _is_teacher_or_student(user):
        raise HTTPException(status_code=403, detail="Solo docentes y estudiantes pueden presentar un proyecto RSU.")
    call = fetch_one(
        """
        SELECT cv.*, ce.nombre AS estado, tp.nombre AS tipo_proyecto, pa.nombre AS periodo_academico
        FROM convocatoria cv
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id
        WHERE cv.convocatoria_id=%s
        """,
        (data.convocatoria_id,),
    )
    if call is None:
        raise HTTPException(status_code=404, detail="Convocatoria no encontrada.")
    if str(call["estado"]).lower() != "abierta" or call["fecha_inicio"] > date.today() or call["fecha_limite_presentacion"] < date.today():
        raise HTTPException(status_code=400, detail="La convocatoria no está disponible para recibir proyectos.")
    if not _project_type_is_rsu(str(call["tipo_proyecto"])):
        raise HTTPException(status_code=400, detail="Los anexos 1 y 2 corresponden a una convocatoria de Proyectos RSU.")
    if user.persona_id in set(data.colaboradores):
        raise HTTPException(status_code=400, detail="El responsable no puede seleccionarse también como colaborador.")
    if len(data.colaboradores) > 5 or len(set(data.colaboradores)) != len(data.colaboradores):
        raise HTTPException(status_code=400, detail="Selecciona como máximo cinco colaboradores diferentes.")
    people = []
    if data.colaboradores:
        placeholders = ",".join(["%s"] * len(data.colaboradores))
        people = fetch_all(
            f"SELECT persona_id FROM persona WHERE activo=1 AND persona_id IN ({placeholders})",
            tuple(data.colaboradores),
        )
    if len(people) != len(data.colaboradores):
        raise HTTPException(status_code=400, detail="Uno o más colaboradores no existen o están inactivos.")

    project_id = str(uuid4())
    state_presented = _state_id("proyecto", "presentado")
    stage_review = _state_id("etapa", "en_revision")
    responsible_role = _role_id("responsable")
    collaborator_role = _role_id("colaborador")
    leadership = user.tipo_usuario.strip().lower()
    budget_json = json.dumps([item.model_dump() for item in data.presupuesto], ensure_ascii=False)
    schedule_json = json.dumps([item.model_dump() for item in data.cronograma], ensure_ascii=False)

    with get_connection() as conn:
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO proyecto
                (proyecto_id, convocatoria_id, periodo_academico_id, linea_accion_id, titulo,
                 tipo_liderazgo, financiado, estado_id, lugar_ejecucion, poblacion_objetivo,
                 resumen, fecha_inicio, fecha_fin, fecha_presentacion)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                """,
                (
                    project_id,
                    data.convocatoria_id,
                    call["periodo_academico_id"],
                    data.linea_accion_id,
                    data.titulo,
                    leadership,
                    int(data.financiado),
                    state_presented,
                    data.lugar_ejecucion,
                    data.poblacion_objetivo,
                    data.resumen,
                    data.fecha_inicio,
                    data.fecha_fin,
                ),
            )
            cur.execute(
                """
                INSERT INTO proyecto_formulario
                (proyecto_id, solicitud_tipo, domicilio_solicitante, condicion_solicitante,
                 problematica, objetivo, metodologia, impacto_social, impacto_estudiantes,
                 cronograma_json, presupuesto_json, revision_actual)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                """,
                (
                    project_id,
                    data.solicitud_tipo,
                    data.domicilio_solicitante,
                    data.condicion_solicitante or user.tipo_usuario,
                    data.problematica,
                    data.objetivo,
                    data.metodologia,
                    data.impacto_social,
                    data.impacto_estudiantes,
                    schedule_json,
                    budget_json,
                ),
            )
            cur.execute(
                """
                INSERT INTO rol_proyecto
                (rol_proyecto_id, persona_id, proyecto_id, rol_id, fecha_asignacion, activo)
                VALUES (%s,%s,%s,%s,%s,1)
                """,
                (str(uuid4()), user.persona_id, project_id, responsible_role, date.today()),
            )
            for person_id in data.colaboradores:
                cur.execute(
                    """
                    INSERT INTO rol_proyecto
                    (rol_proyecto_id, persona_id, proyecto_id, rol_id, fecha_asignacion, activo)
                    VALUES (%s,%s,%s,%s,%s,1)
                    """,
                    (str(uuid4()), person_id, project_id, collaborator_role, date.today()),
                )
            for item in data.presupuesto:
                total = item.cantidad * item.costo_unitario
                cur.execute(
                    """
                    INSERT INTO financiamiento
                    (financiamiento_id, proyecto_id, partida, descripcion, unidad_medida,
                     cantidad, costo_unitario, monto_total, aprobado)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0)
                    """,
                    (str(uuid4()), project_id, "insumos_materiales", item.bien_servicio, item.unidad_medida, item.cantidad, item.costo_unitario, total),
                )
            cur.execute(
                """
                INSERT INTO etapa_proyecto
                (etapa_proyecto_id, proyecto_id, nombre, estado_id, semana_limite, fecha_limite, observaciones)
                VALUES (%s,%s,'Presentación y evaluación',%s,2,%s,NULL)
                """,
                (str(uuid4()), project_id, stage_review, call["fecha_limite_presentacion"]),
            )
            conn.commit()
            cur.close()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    await ws_manager.broadcast(
        {
            "type": "proyecto.presentado",
            "audience": ["administrativo"],
            "message": f"Nuevo proyecto pendiente de evaluación: {data.titulo}",
            "data": {"proyecto_id": project_id, "responsable": user.nombre_completo},
        }
    )
    return _serialize_project(project_id, user)


@router.get("/proyectos/mios")
def my_projects(user: AuthUser = Depends(get_current_user)):
    return fetch_all(
        """
        SELECT DISTINCT p.proyecto_id, p.titulo, p.fecha_presentacion, p.fecha_creacion,
               ce.nombre AS estado, la.nombre AS linea_accion, pa.nombre AS periodo_academico,
               pf.revision_actual,
               (SELECT ep.observaciones FROM evaluacion_proyecto ep
                WHERE ep.proyecto_id=p.proyecto_id ORDER BY ep.fecha_evaluacion DESC LIMIT 1) AS ultima_observacion
        FROM proyecto p
        INNER JOIN rol_proyecto rp ON rp.proyecto_id=p.proyecto_id
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=p.periodo_academico_id
        LEFT JOIN proyecto_formulario pf ON pf.proyecto_id=p.proyecto_id
        WHERE rp.persona_id=%s AND LOWER(cr.nombre)='responsable' AND rp.activo=1
        ORDER BY p.fecha_creacion DESC
        """,
        (user.persona_id,),
    )


@router.get("/proyectos/pendientes")
def pending_projects(_: AuthUser = Depends(require_admin)):
    return fetch_all(
        """
        SELECT p.proyecto_id, p.titulo, p.fecha_presentacion, p.fecha_creacion,
               ce.nombre AS estado, la.nombre AS linea_accion, pa.nombre AS periodo_academico,
               pf.revision_actual,
               CONCAT(pe.nombres,' ',pe.apellidos) AS responsable, pe.dni AS responsable_dni,
               (SELECT ep.observaciones FROM evaluacion_proyecto ep
                WHERE ep.proyecto_id=p.proyecto_id ORDER BY ep.fecha_evaluacion DESC LIMIT 1) AS ultima_observacion
        FROM proyecto p
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=p.periodo_academico_id
        LEFT JOIN proyecto_formulario pf ON pf.proyecto_id=p.proyecto_id
        INNER JOIN rol_proyecto rp ON rp.proyecto_id=p.proyecto_id
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id AND LOWER(cr.nombre)='responsable'
        INNER JOIN persona pe ON pe.persona_id=rp.persona_id
        WHERE LOWER(ce.nombre) IN ('presentado','en_evaluacion')
        ORDER BY p.fecha_presentacion DESC, p.fecha_creacion DESC
        """
    )


@router.get("/proyectos/{project_id}")
def project_detail(project_id: str, user: AuthUser = Depends(get_current_user)):
    return _serialize_project(project_id, user)


@router.put("/proyectos/{project_id}/corregir")
async def correct_project(project_id: str, data: ProyectoFormularioIn, user: AuthUser = Depends(get_current_user)):
    if fetch_one("SELECT 1 FROM rsu_project_workflow WHERE proyecto_id=%s", (project_id,)):
        raise HTTPException(status_code=409, detail="La subsanación debe realizarse desde el expediente del ciclo RSU.")
    project = _assert_project_access(project_id, user, admin_allowed=False)
    if str(project["estado"]).lower() != "con_observaciones":
        raise HTTPException(status_code=400, detail="Solo se puede corregir un proyecto que tenga observaciones.")
    if user.persona_id in set(data.colaboradores) or len(data.colaboradores) > 5 or len(set(data.colaboradores)) != len(data.colaboradores):
        raise HTTPException(status_code=400, detail="Selecciona como máximo cinco colaboradores diferentes del responsable.")
    people = []
    if data.colaboradores:
        placeholders = ",".join(["%s"] * len(data.colaboradores))
        people = fetch_all(f"SELECT persona_id FROM persona WHERE activo=1 AND persona_id IN ({placeholders})", tuple(data.colaboradores))
    if len(people) != len(data.colaboradores):
        raise HTTPException(status_code=400, detail="Uno o más colaboradores no existen o están inactivos.")
    collaborator_role = _role_id("colaborador")
    state_presented = _state_id("proyecto", "presentado")
    stage_review = _state_id("etapa", "en_revision")
    schedule_json = json.dumps([item.model_dump() for item in data.cronograma], ensure_ascii=False)
    budget_json = json.dumps([item.model_dump() for item in data.presupuesto], ensure_ascii=False)

    with get_connection() as conn:
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE proyecto SET linea_accion_id=%s, titulo=%s, financiado=%s, estado_id=%s,
                    lugar_ejecucion=%s, poblacion_objetivo=%s, resumen=%s,
                    fecha_inicio=%s, fecha_fin=%s, fecha_presentacion=NOW()
                WHERE proyecto_id=%s
                """,
                (data.linea_accion_id, data.titulo, int(data.financiado), state_presented, data.lugar_ejecucion,
                 data.poblacion_objetivo, data.resumen, data.fecha_inicio, data.fecha_fin, project_id),
            )
            cur.execute(
                """
                UPDATE proyecto_formulario SET solicitud_tipo=%s, domicilio_solicitante=%s,
                    condicion_solicitante=%s, problematica=%s, objetivo=%s, metodologia=%s,
                    impacto_social=%s, impacto_estudiantes=%s, cronograma_json=%s,
                    presupuesto_json=%s, revision_actual=revision_actual+1
                WHERE proyecto_id=%s
                """,
                (data.solicitud_tipo, data.domicilio_solicitante, data.condicion_solicitante,
                 data.problematica, data.objetivo, data.metodologia, data.impacto_social,
                 data.impacto_estudiantes, schedule_json, budget_json, project_id),
            )
            cur.execute(
                """
                DELETE rp FROM rol_proyecto rp
                INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
                WHERE rp.proyecto_id=%s AND LOWER(cr.nombre)='colaborador'
                """,
                (project_id,),
            )
            for person_id in data.colaboradores:
                cur.execute(
                    """INSERT INTO rol_proyecto
                    (rol_proyecto_id, persona_id, proyecto_id, rol_id, fecha_asignacion, activo)
                    VALUES (%s,%s,%s,%s,%s,1)""",
                    (str(uuid4()), person_id, project_id, collaborator_role, date.today()),
                )
            cur.execute("DELETE FROM financiamiento WHERE proyecto_id=%s", (project_id,))
            for item in data.presupuesto:
                total = item.cantidad * item.costo_unitario
                cur.execute(
                    """INSERT INTO financiamiento
                    (financiamiento_id, proyecto_id, partida, descripcion, unidad_medida,
                     cantidad, costo_unitario, monto_total, aprobado)
                    VALUES (%s,%s,'insumos_materiales',%s,%s,%s,%s,%s,0)""",
                    (str(uuid4()), project_id, item.bien_servicio, item.unidad_medida, item.cantidad, item.costo_unitario, total),
                )
            cur.execute(
                """
                UPDATE etapa_proyecto SET estado_id=%s, observaciones=NULL, fecha_completado=NULL
                WHERE proyecto_id=%s AND nombre='Presentación y evaluación'
                """,
                (stage_review, project_id),
            )
            conn.commit()
            cur.close()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ws_manager.broadcast(
        {"type": "proyecto.corregido", "audience": ["administrativo"], "message": f"Proyecto corregido y reenviado: {data.titulo}", "data": {"proyecto_id": project_id}}
    )
    return _serialize_project(project_id, user)


@router.post("/proyectos/{project_id}/evaluar")
async def evaluate_project(project_id: str, data: EvaluacionProyectoIn, admin: AuthUser = Depends(require_admin)):
    if fetch_one("SELECT 1 FROM rsu_project_workflow WHERE proyecto_id=%s", (project_id,)):
        raise HTTPException(status_code=409, detail="La evaluación debe registrarse mediante el Anexo 4 o Anexo 7 del expediente.")
    project = _assert_project_access(project_id, admin)
    if str(project["estado"]).lower() not in {"presentado", "en_evaluacion"}:
        raise HTTPException(status_code=400, detail="El proyecto no se encuentra pendiente de evaluación.")
    new_state = _state_id("proyecto", data.resultado)
    stage_state = _state_id("etapa", "completado" if data.resultado == "aprobado" else "con_observaciones")
    form = fetch_one("SELECT revision_actual FROM proyecto_formulario WHERE proyecto_id=%s", (project_id,)) or {"revision_actual": 1}
    responsible_user = fetch_one(
        """
        SELECT u.usuario_id
        FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        INNER JOIN usuario u ON u.persona_id=rp.persona_id AND u.activo=1
        WHERE rp.proyecto_id=%s AND LOWER(cr.nombre)='responsable' LIMIT 1
        """,
        (project_id,),
    )
    with get_connection() as conn:
        try:
            cur = conn.cursor()
            cur.execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (new_state, project_id))
            cur.execute(
                """
                INSERT INTO evaluacion_proyecto
                (evaluacion_proyecto_id, proyecto_id, evaluador_persona_id, resultado, observaciones, revision)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (str(uuid4()), project_id, admin.persona_id, data.resultado, data.observaciones, int(form["revision_actual"])),
            )
            cur.execute(
                """
                UPDATE etapa_proyecto SET estado_id=%s, observaciones=%s,
                    fecha_completado=CURDATE(),
                    fecha_limite_observaciones=CASE WHEN %s='con_observaciones' THEN DATE_ADD(CURDATE(), INTERVAL 3 DAY) ELSE NULL END
                WHERE proyecto_id=%s AND nombre='Presentación y evaluación'
                """,
                (stage_state, data.observaciones, data.resultado, project_id),
            )
            conn.commit()
            cur.close()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = {
        "type": "proyecto.evaluado",
        "message": "Tu proyecto fue aprobado." if data.resultado == "aprobado" else "Tu proyecto tiene observaciones para corregir.",
        "data": {"proyecto_id": project_id, "resultado": data.resultado, "observaciones": data.observaciones},
    }
    if responsible_user:
        await ws_manager.send_to_user(responsible_user["usuario_id"], payload)
    return _serialize_project(project_id, admin)


@router.get("/proyectos/{project_id}/pdf")
def download_project_pdf(project_id: str, user: AuthUser = Depends(get_current_user)):
    data = _serialize_project(project_id, user)
    contact = fetch_one(
        """
        SELECT c.direccion, d.condicion
        FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        INNER JOIN persona p ON p.persona_id=rp.persona_id
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        WHERE rp.proyecto_id=%s AND LOWER(cr.nombre)='responsable' LIMIT 1
        """,
        (project_id,),
    ) or {}
    responsible = data.get("responsable") or {}
    pdf_data = dict(data)
    pdf_data["responsable_nombre"] = responsible.get("nombre_completo", "")
    pdf_data["responsable_dni"] = responsible.get("dni", "")
    pdf_data["domicilio_solicitante"] = data.get("domicilio_solicitante") or contact.get("direccion") or "No registrado"
    pdf_data["condicion_solicitante"] = data.get("condicion_solicitante") or contact.get("condicion") or data.get("tipo_liderazgo")
    settings = get_settings()
    # Los PDF generados contienen datos personales. Se guardan fuera de /uploads,
    # que en la aplicación original se publica como carpeta estática.
    output = Path(settings.upload_dir).resolve().parent / "generated_pdfs_private" / f"anexos_1_2_{project_id}.pdf"
    generate_annexes_pdf(output, pdf_data)
    safe_title = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(data.get("titulo") or "proyecto"))[:80]
    return FileResponse(path=output, media_type="application/pdf", filename=f"{safe_title}-anexo-1-2.pdf")
