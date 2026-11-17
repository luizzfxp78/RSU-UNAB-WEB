from datetime import date
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.db.mysql import execute, fetch_all, fetch_one, get_connection
from app.schemas.auth import AuthUser
from app.schemas.projects import EtapaCreate, JornadaVoluntariadoCreate, ProyectoCreate, ProyectoEstadoPatch
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/proyectos", tags=["Proyectos RSU"])


def _estado_por_nombre(entidad: str, nombre: str) -> str | None:
    row = fetch_one(
        "SELECT catalogo_estado_id FROM catalogo_estado WHERE entidad=%s AND LOWER(nombre)=LOWER(%s) LIMIT 1",
        (entidad, nombre),
    )
    return row["catalogo_estado_id"] if row else None


@router.get("")
def listar_proyectos(
    estado_id: str | None = None,
    linea_accion_id: str | None = None,
    periodo_academico_id: str | None = None,
    _: AuthUser = Depends(get_current_user),
):
    where_parts: list[str] = []
    params: list[str] = []
    if estado_id:
        where_parts.append("p.estado_id = %s")
        params.append(estado_id)
    if linea_accion_id:
        where_parts.append("p.linea_accion_id = %s")
        params.append(linea_accion_id)
    if periodo_academico_id:
        where_parts.append("p.periodo_academico_id = %s")
        params.append(periodo_academico_id)
    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    return fetch_all(
        f"""
        SELECT
            p.*, la.codigo AS linea_codigo, la.nombre AS linea_accion,
            pa.nombre AS periodo_academico, ce.nombre AS estado,
            cv.tipo_proyecto_id, tp.nombre AS tipo_proyecto
        FROM proyecto p
        INNER JOIN linea_accion la ON la.linea_accion_id = p.linea_accion_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id = p.periodo_academico_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id = p.estado_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id = p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id = cv.tipo_proyecto_id
        {where}
        ORDER BY p.fecha_creacion DESC
        LIMIT 300
        """,
        tuple(params),
    )


@router.post("")
async def crear_proyecto(data: ProyectoCreate, current_user: AuthUser = Depends(get_current_user)):
    call_type = fetch_one(
        """SELECT tp.nombre FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        WHERE cv.convocatoria_id=%s""",
        (data.convocatoria_id,),
    )
    if call_type and "responsabilidad social universitaria" in str(call_type["nombre"]).lower():
        raise HTTPException(
            status_code=409,
            detail="Los proyectos RSU deben registrarse mediante el flujo de Anexos 1 y 2 en /ciclo-rsu/proyectos.",
        )
    proyecto_id = str(uuid4())
    estado_id = data.estado_id or _estado_por_nombre("proyecto", "borrador") or _estado_por_nombre("proyecto", "presentado")
    if estado_id is None:
        raise HTTPException(status_code=400, detail="No existe estado inicial para proyecto en catalogo_estado.")

    with get_connection() as conn:
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO proyecto
                (proyecto_id, convocatoria_id, periodo_academico_id, linea_accion_id, titulo,
                 tipo_liderazgo, financiado, estado_id, lugar_ejecucion, poblacion_objetivo,
                 resumen, fecha_inicio, fecha_fin, fecha_presentacion)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    proyecto_id,
                    data.convocatoria_id,
                    data.periodo_academico_id,
                    data.linea_accion_id,
                    data.titulo,
                    data.tipo_liderazgo,
                    int(data.financiado),
                    estado_id,
                    data.lugar_ejecucion,
                    data.poblacion_objetivo,
                    data.resumen,
                    data.fecha_inicio,
                    data.fecha_fin,
                ),
            )
            for part in data.participantes:
                cur.execute(
                    """
                    INSERT INTO rol_proyecto
                    (rol_proyecto_id, persona_id, proyecto_id, rol_id, horas_semanales, fecha_asignacion, activo)
                    VALUES (%s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        str(uuid4()),
                        part.persona_id,
                        proyecto_id,
                        part.rol_id,
                        part.horas_semanales,
                        part.fecha_asignacion or date.today(),
                    ),
                )
            for item in data.financiamiento:
                cur.execute(
                    """
                    INSERT INTO financiamiento
                    (financiamiento_id, proyecto_id, partida, descripcion, unidad_medida, cantidad,
                     costo_unitario, monto_total, aprobado)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid4()),
                        proyecto_id,
                        item.partida,
                        item.descripcion,
                        item.unidad_medida,
                        item.cantidad,
                        item.costo_unitario,
                        item.monto_total,
                        int(item.aprobado),
                    ),
                )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = {"type": "project.created", "message": "Nuevo proyecto registrado.", "data": {"proyecto_id": proyecto_id, "titulo": data.titulo}}
    await ws_manager.broadcast(payload)
    return obtener_proyecto(proyecto_id, current_user)


@router.get("/{proyecto_id}")
def obtener_proyecto(proyecto_id: str, _: AuthUser = Depends(get_current_user)):
    proyecto = fetch_one(
        """
        SELECT p.*, la.nombre AS linea_accion, pa.nombre AS periodo_academico, ce.nombre AS estado
        FROM proyecto p
        INNER JOIN linea_accion la ON la.linea_accion_id = p.linea_accion_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id = p.periodo_academico_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id = p.estado_id
        WHERE p.proyecto_id = %s
        """,
        (proyecto_id,),
    )
    if proyecto is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")

    participantes = fetch_all(
        """
        SELECT rp.*, CONCAT(pe.nombres, ' ', pe.apellidos) AS nombre_completo, pe.dni, cr.nombre AS rol
        FROM rol_proyecto rp
        INNER JOIN persona pe ON pe.persona_id = rp.persona_id
        INNER JOIN catalogo_rol cr ON cr.rol_id = rp.rol_id
        WHERE rp.proyecto_id = %s
        ORDER BY cr.nombre, pe.apellidos
        """,
        (proyecto_id,),
    )
    financiamiento = fetch_all("SELECT * FROM financiamiento WHERE proyecto_id = %s", (proyecto_id,))
    etapas = fetch_all(
        """
        SELECT ep.*, ce.nombre AS estado
        FROM etapa_proyecto ep
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id = ep.estado_id
        WHERE ep.proyecto_id = %s
        ORDER BY ep.semana_limite, ep.nombre
        """,
        (proyecto_id,),
    )
    return {"proyecto": proyecto, "participantes": participantes, "financiamiento": financiamiento, "etapas": etapas}


@router.patch("/{proyecto_id}/estado")
async def cambiar_estado(proyecto_id: str, data: ProyectoEstadoPatch, current_user: AuthUser = Depends(get_current_user)):
    if fetch_one("SELECT 1 FROM rsu_project_workflow WHERE proyecto_id=%s", (proyecto_id,)):
        raise HTTPException(
            status_code=409,
            detail="El estado de este proyecto se controla por el ciclo institucional de anexos y no puede modificarse directamente.",
        )
    proyecto = fetch_one("SELECT estado_id, titulo FROM proyecto WHERE proyecto_id = %s", (proyecto_id,))
    if proyecto is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    execute("UPDATE proyecto SET estado_id = %s WHERE proyecto_id = %s", (data.estado_id, proyecto_id))
    await ws_manager.broadcast(
        {
            "type": "project.state_changed",
            "message": "Estado de proyecto actualizado.",
            "data": {"proyecto_id": proyecto_id, "estado_anterior_id": proyecto["estado_id"], "estado_nuevo_id": data.estado_id, "usuario": current_user.nombre_completo},
        }
    )
    return obtener_proyecto(proyecto_id, current_user)


@router.post("/{proyecto_id}/etapas")
async def crear_etapa(proyecto_id: str, data: EtapaCreate, current_user: AuthUser = Depends(get_current_user)):
    if fetch_one("SELECT 1 FROM rsu_project_workflow WHERE proyecto_id=%s", (proyecto_id,)):
        raise HTTPException(
            status_code=409,
            detail="Las etapas del proyecto RSU se crean automáticamente según el procedimiento institucional.",
        )
    if fetch_one("SELECT proyecto_id FROM proyecto WHERE proyecto_id = %s", (proyecto_id,)) is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    etapa_id = str(uuid4())
    execute(
        """
        INSERT INTO etapa_proyecto
        (etapa_proyecto_id, proyecto_id, nombre, estado_id, semana_limite, fecha_limite, observaciones)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (etapa_id, proyecto_id, data.nombre, data.estado_id, data.semana_limite, data.fecha_limite, data.observaciones),
    )
    await ws_manager.broadcast({"type": "project.stage_created", "message": "Etapa registrada.", "data": {"proyecto_id": proyecto_id, "etapa_id": etapa_id}})
    return obtener_proyecto(proyecto_id, current_user)


@router.post("/jornadas-voluntariado")
async def registrar_jornada(data: JornadaVoluntariadoCreate, _: AuthUser = Depends(get_current_user)):
    jornada_id = str(uuid4())
    execute(
        """
        INSERT INTO jornada_voluntariado
        (jornada_voluntaria_id, rol_proyecto_id, fecha, horas, descripcion, evidencia_url)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (jornada_id, data.rol_proyecto_id, data.fecha, data.horas, data.descripcion, data.evidencia_url),
    )
    execute(
        """
        UPDATE rol_proyecto rp
        SET rp.horas_acumuladas=(
            SELECT ROUND(COALESCE(SUM(j.horas),0),0)
            FROM jornada_voluntariado j
            WHERE j.rol_proyecto_id=rp.rol_proyecto_id
        )
        WHERE rp.rol_proyecto_id=%s
        """,
        (data.rol_proyecto_id,),
    )
    await ws_manager.broadcast({"type": "volunteering.hours_registered", "message": "Jornada de voluntariado registrada.", "data": {"jornada_id": jornada_id}})
    return fetch_one("SELECT * FROM jornada_voluntariado WHERE jornada_voluntaria_id = %s", (jornada_id,))
