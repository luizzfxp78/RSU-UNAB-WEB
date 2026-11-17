from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from fastapi import HTTPException

from app.db.mysql import fetch_all, fetch_one
from app.schemas.auth import AuthUser


ADMIN_TYPES = {"administrativo", "administrador", "admin"}


def _is_admin(user: AuthUser) -> bool:
    role = str(user.rol_sistema or "").strip().lower()
    if role:
        return role in {"administrador_rsu", "director_rsu"}
    return user.tipo_usuario.strip().lower() in ADMIN_TYPES


def _permissions(user: AuthUser) -> dict[str, bool]:
    admin = _is_admin(user)
    kind = user.tipo_usuario.strip().lower()
    return {
        "is_admin": admin,
        "can_manage_calls": admin,
        "can_register_people": admin,
        "can_manage_personnel": admin,
        "can_evaluate": admin,
        "can_view_reports": admin,
        "can_submit_project": kind in {"docente", "estudiante"},
    }


def _require_admin(user: AuthUser) -> None:
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Este módulo está disponible únicamente para personal autorizado.")


def _project_scope(user: AuthUser, alias: str = "p") -> tuple[str, str, tuple[Any, ...]]:
    if _is_admin(user):
        return "", "1=1", ()
    joins = f"""
        INNER JOIN rol_proyecto rp_scope ON rp_scope.proyecto_id={alias}.proyecto_id
        INNER JOIN catalogo_rol cr_scope ON cr_scope.rol_id=rp_scope.rol_id
    """
    where = "rp_scope.persona_id=%s AND rp_scope.activo=1"
    return joins, where, (user.persona_id,)


def _event_scope_sql(user: AuthUser, project_alias: str) -> tuple[str, tuple[Any, ...]]:
    if _is_admin(user):
        return "1=1", ()
    return (
        f"EXISTS (SELECT 1 FROM rol_proyecto rp_evt WHERE rp_evt.proyecto_id={project_alias}.proyecto_id "
        "AND rp_evt.persona_id=%s AND rp_evt.activo=1)",
        (user.persona_id,),
    )


def _convocation_rows(limit: int = 60) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT cv.convocatoria_id, tp.nombre AS tipo_proyecto,
               pa.nombre AS periodo_academico, cv.fecha_inicio,
               cv.fecha_limite_presentacion, cv.monto_maximo_financiamiento,
               cv.bases_url, cv.fecha_creacion,
               CASE
                   WHEN LOWER(ce.nombre)='abierta' AND cv.fecha_inicio <= CURDATE()
                        AND cv.fecha_limite_presentacion >= CURDATE() THEN 'Abierta'
                   WHEN cv.fecha_inicio > CURDATE() THEN 'Próxima'
                   WHEN cv.fecha_limite_presentacion < CURDATE() THEN 'Cerrada'
                   ELSE ce.nombre
               END AS estado
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        ORDER BY cv.fecha_inicio DESC, cv.fecha_creacion DESC
        LIMIT {int(limit)}
        """
    )


def _project_rows(user: AuthUser, type_like: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
    joins, scope_where, params = _project_scope(user)
    type_clause = ""
    values: list[Any] = list(params)
    if type_like:
        type_clause = "AND LOWER(tp.nombre) LIKE %s"
        values.append(f"%{type_like.lower()}%")
    return fetch_all(
        f"""
        SELECT p.proyecto_id, p.titulo, p.lugar_ejecucion, p.poblacion_objetivo,
               p.fecha_inicio, p.fecha_fin, p.fecha_presentacion, p.fecha_creacion,
               ce.nombre AS estado, tp.nombre AS modalidad,
               la.nombre AS linea_accion, la.codigo AS linea_codigo,
               resp.responsable
        FROM proyecto p
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        {joins}
        LEFT JOIN (
            SELECT rp.proyecto_id, CONCAT(pe.nombres,' ',pe.apellidos) AS responsable
            FROM rol_proyecto rp
            INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id AND LOWER(cr.nombre)='responsable'
            INNER JOIN persona pe ON pe.persona_id=rp.persona_id
            WHERE rp.activo=1
        ) resp ON resp.proyecto_id=p.proyecto_id
        WHERE {scope_where} {type_clause}
        ORDER BY COALESCE(p.fecha_presentacion,p.fecha_creacion) DESC
        LIMIT {int(limit)}
        """,
        tuple(values),
    )


def _project_distribution(user: AuthUser, field: str, type_like: str | None = None) -> list[dict[str, Any]]:
    joins, scope_where, params = _project_scope(user)
    values: list[Any] = list(params)
    type_clause = ""
    if type_like:
        type_clause = "AND LOWER(tp.nombre) LIKE %s"
        values.append(f"%{type_like.lower()}%")
    if field == "estado":
        select = "ce.nombre AS etiqueta"
        group = "ce.catalogo_estado_id, ce.nombre"
    elif field == "modalidad":
        select = "tp.nombre AS etiqueta"
        group = "tp.tipo_proyecto_id, tp.nombre"
    else:
        select = "la.nombre AS etiqueta"
        group = "la.linea_accion_id, la.nombre"
    return fetch_all(
        f"""
        SELECT {select}, COUNT(DISTINCT p.proyecto_id) AS cantidad
        FROM proyecto p
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        {joins}
        WHERE {scope_where} {type_clause}
        GROUP BY {group}
        ORDER BY cantidad DESC, etiqueta
        """,
        tuple(values),
    )


def _project_trend(user: AuthUser, type_like: str | None = None) -> list[dict[str, Any]]:
    joins, scope_where, params = _project_scope(user)
    values: list[Any] = list(params)
    type_clause = ""
    if type_like:
        type_clause = "AND LOWER(tp.nombre) LIKE %s"
        values.append(f"%{type_like.lower()}%")
    return fetch_all(
        f"""
        SELECT DATE_FORMAT(COALESCE(p.fecha_presentacion,p.fecha_creacion), '%Y-%m') AS periodo,
               COUNT(DISTINCT p.proyecto_id) AS registrados,
               COUNT(DISTINCT CASE WHEN LOWER(ce.nombre)='aprobado' THEN p.proyecto_id END) AS aprobados
        FROM proyecto p
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        {joins}
        WHERE {scope_where}
          AND COALESCE(p.fecha_presentacion,p.fecha_creacion) >= DATE_SUB(CURDATE(), INTERVAL 11 MONTH)
          {type_clause}
        GROUP BY periodo
        ORDER BY periodo
        """,
        tuple(values),
    )


def _recent_events(user: AuthUser, limit: int = 12) -> list[dict[str, Any]]:
    project_condition, project_params = _event_scope_sql(user, "p")
    eval_condition, eval_params = _event_scope_sql(user, "p")
    cert_condition, cert_params = _event_scope_sql(user, "p")
    values: list[Any] = []
    values.extend(project_params)
    values.extend(eval_params)
    values.extend(cert_params)
    # Convocatorias son institucionales y visibles para cualquier usuario autenticado.
    return fetch_all(
        f"""
        SELECT * FROM (
            SELECT 'convocatoria' AS tipo,
                   'Convocatoria registrada' AS titulo,
                   CONCAT(tp.nombre, ' · ', pa.nombre) AS mensaje,
                   cv.fecha_creacion AS fecha,
                   cv.convocatoria_id AS referencia_id
            FROM convocatoria cv
            INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
            INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id

            UNION ALL

            SELECT 'proyecto' AS tipo,
                   CASE WHEN LOWER(ce.nombre)='con_observaciones' THEN 'Proyecto observado'
                        WHEN LOWER(ce.nombre)='aprobado' THEN 'Proyecto aprobado'
                        ELSE 'Proyecto registrado' END AS titulo,
                   p.titulo AS mensaje,
                   COALESCE(p.fecha_presentacion,p.fecha_creacion) AS fecha,
                   p.proyecto_id AS referencia_id
            FROM proyecto p
            INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
            WHERE {project_condition}

            UNION ALL

            SELECT 'evaluacion' AS tipo,
                   CASE WHEN ep.resultado='aprobado' THEN 'Evaluación aprobatoria'
                        ELSE 'Observaciones emitidas' END AS titulo,
                   p.titulo AS mensaje,
                   ep.fecha_evaluacion AS fecha,
                   p.proyecto_id AS referencia_id
            FROM evaluacion_proyecto ep
            INNER JOIN proyecto p ON p.proyecto_id=ep.proyecto_id
            WHERE {eval_condition}

            UNION ALL

            SELECT 'certificado' AS tipo,
                   'Certificado emitido' AS titulo,
                   CONCAT(p.titulo, ' · ', pe.nombres, ' ', pe.apellidos) AS mensaje,
                   COALESCE(c.fecha_emision,c.fecha_solicitud) AS fecha,
                   p.proyecto_id AS referencia_id
            FROM certificado c
            INNER JOIN proyecto p ON p.proyecto_id=c.proyecto_id
            INNER JOIN persona pe ON pe.persona_id=c.persona_id
            WHERE COALESCE(c.fecha_emision,c.fecha_solicitud) IS NOT NULL AND {cert_condition}
        ) eventos
        WHERE fecha IS NOT NULL
        ORDER BY fecha DESC
        LIMIT {int(limit)}
        """,
        tuple(values),
    )


def _inicio(user: AuthUser) -> dict[str, Any]:
    calls = _convocation_rows(8)
    if not _is_admin(user):
        calls = [row for row in calls if str(row.get("estado") or "").strip().lower() == "abierta"]
    projects = _project_rows(user, limit=10)
    status = _project_distribution(user, "estado")
    modality = _project_distribution(user, "modalidad")
    trend = _project_trend(user)

    active_calls = sum(1 for row in calls if str(row.get("estado", "")).lower() == "abierta")
    counts = {str(row.get("etiqueta", "")).lower().replace(" ", "_"): int(row.get("cantidad") or 0) for row in status}
    total = sum(int(row.get("cantidad") or 0) for row in status)

    review_rows = [
        row for row in projects
        if str(row.get("estado") or "").strip().lower().replace(" ", "_")
        in {"presentado", "en_evaluacion", "en_evaluación", "con_observaciones"}
    ]
    weekly = fetch_all(
        """
        SELECT WEEKDAY(COALESCE(fecha_presentacion,fecha_creacion)) AS dia,
               COUNT(*) AS cantidad
        FROM proyecto
        WHERE COALESCE(fecha_presentacion,fecha_creacion) >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
        GROUP BY dia ORDER BY dia
        """
    ) if _is_admin(user) else []

    return {
        "module": "inicio",
        "permissions": _permissions(user),
        "counts": {
            "convocatorias_activas": active_calls,
            "proyectos_en_evaluacion": counts.get("presentado", 0) + counts.get("en_evaluacion", 0) + counts.get("en_evaluación", 0),
            "pendientes_subsanacion": counts.get("con_observaciones", 0),
            "proyectos_aprobados": counts.get("aprobado", 0),
            "total_proyectos": total,
        },
        "rows": review_rows[:8],
        "convocatorias": calls,
        "status_distribution": status,
        "secondary_distribution": modality,
        "trend": trend,
        "weekly": weekly,
        "events": _recent_events(user, 8),
        "meta": {"generated_at": datetime.now().isoformat()},
    }


def _convocatorias(user: AuthUser) -> dict[str, Any]:
    rows = _convocation_rows(100)
    # Docentes y estudiantes solo visualizan convocatorias realmente vigentes.
    # El personal administrativo conserva el histórico para gestión y auditoría.
    if not _is_admin(user):
        rows = [row for row in rows if str(row.get("estado") or "").strip().lower() == "abierta"]
    counts = {"abiertas": 0, "proximas": 0, "en_evaluacion": 0, "cerradas": 0}
    distribution: dict[str, int] = {}
    for row in rows:
        status = str(row.get("estado") or "").strip().lower()
        if status == "abierta":
            counts["abiertas"] += 1
        elif status in {"próxima", "proxima"}:
            counts["proximas"] += 1
        elif "evalu" in status:
            counts["en_evaluacion"] += 1
        else:
            counts["cerradas"] += 1
        distribution[str(row.get("tipo_proyecto") or "Sin modalidad")] = distribution.get(str(row.get("tipo_proyecto") or "Sin modalidad"), 0) + 1
    key_dates: list[dict[str, Any]] = []
    for row in rows:
        for kind, value in (("Inicio", row.get("fecha_inicio")), ("Cierre", row.get("fecha_limite_presentacion"))):
            if value and value >= date.today():
                key_dates.append({
                    "tipo": kind,
                    "fecha": value,
                    "titulo": f"{row.get('tipo_proyecto')} · {row.get('periodo_academico')}",
                    "estado": row.get("estado"),
                    "referencia_id": row.get("convocatoria_id"),
                })
    key_dates.sort(key=lambda item: item["fecha"])
    trend = fetch_all(
        """
        SELECT DATE_FORMAT(fecha_creacion,'%Y-%m') AS periodo, COUNT(*) AS total
        FROM convocatoria
        WHERE fecha_creacion >= DATE_SUB(CURDATE(), INTERVAL 11 MONTH)
        GROUP BY periodo ORDER BY periodo
        """
    )
    return {
        "module": "convocatorias",
        "permissions": _permissions(user),
        "counts": counts,
        "rows": rows,
        "status_distribution": [{"etiqueta": key, "cantidad": value} for key, value in distribution.items()],
        "trend": trend,
        "side_items": key_dates[:8],
        "events": _recent_events(user, 8),
    }


def _proyectos(user: AuthUser) -> dict[str, Any]:
    rows = _project_rows(user, limit=100)
    status = _project_distribution(user, "estado")
    lines = _project_distribution(user, "linea")
    trend = _project_trend(user)
    counts_map = {str(row.get("etiqueta") or "").lower().replace(" ", "_"): int(row.get("cantidad") or 0) for row in status}
    return {
        "module": "proyectos",
        "permissions": _permissions(user),
        "counts": {
            "borrador": counts_map.get("borrador", 0),
            "en_evaluacion": counts_map.get("presentado", 0) + counts_map.get("en_evaluacion", 0) + counts_map.get("en_evaluación", 0),
            "observados": counts_map.get("con_observaciones", 0) + counts_map.get("observado", 0),
            "aprobados": counts_map.get("aprobado", 0),
        },
        "rows": rows,
        "status_distribution": status,
        "secondary_distribution": lines,
        "trend": trend,
        "events": _recent_events(user, 8),
    }


def _actividades(user: AuthUser) -> dict[str, Any]:
    rows = _project_rows(user, "actividades específicas", 100)
    status = _project_distribution(user, "estado", "actividades específicas")
    lines = _project_distribution(user, "linea", "actividades específicas")
    trend = _project_trend(user, "actividades específicas")
    counts_map = {str(row.get("etiqueta") or "").lower().replace(" ", "_"): int(row.get("cantidad") or 0) for row in status}
    agenda = [row for row in rows if row.get("fecha_inicio") and row.get("fecha_inicio") >= date.today()]
    agenda.sort(key=lambda row: row.get("fecha_inicio"))
    return {
        "module": "actividades",
        "permissions": _permissions(user),
        "counts": {
            "registradas": len(rows),
            "planificacion": counts_map.get("borrador", 0) + counts_map.get("presentado", 0),
            "ejecucion": counts_map.get("en_ejecucion", 0) + counts_map.get("aprobado", 0),
            "finalizadas": counts_map.get("finalizado", 0),
        },
        "rows": rows,
        "status_distribution": status,
        "secondary_distribution": lines,
        "trend": trend,
        "side_items": agenda[:8],
        "events": [event for event in _recent_events(user, 12) if event.get("referencia_id") in {row.get("proyecto_id") for row in rows}][:8],
    }


def _voluntariado(user: AuthUser) -> dict[str, Any]:
    project_rows = _project_rows(user, "voluntariado", 100)
    joins, scope_where, params = _project_scope(user)
    # Las jornadas se agrupan por proyecto, fecha y descripción para no repetirlas por participante.
    journeys = fetch_all(
        f"""
        SELECT p.proyecto_id, p.titulo AS actividad, p.lugar_ejecucion AS lugar,
               j.fecha, j.descripcion,
               COUNT(DISTINCT j.rol_proyecto_id) AS participantes,
               ROUND(SUM(j.horas),1) AS horas,
               ce.nombre AS estado, resp.responsable
        FROM jornada_voluntariado j
        INNER JOIN rol_proyecto rp ON rp.rol_proyecto_id=j.rol_proyecto_id
        INNER JOIN proyecto p ON p.proyecto_id=rp.proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        {joins}
        LEFT JOIN (
            SELECT rp2.proyecto_id, CONCAT(pe.nombres,' ',pe.apellidos) AS responsable
            FROM rol_proyecto rp2
            INNER JOIN catalogo_rol cr2 ON cr2.rol_id=rp2.rol_id AND LOWER(cr2.nombre)='responsable'
            INNER JOIN persona pe ON pe.persona_id=rp2.persona_id
            WHERE rp2.activo=1
        ) resp ON resp.proyecto_id=p.proyecto_id
        WHERE {scope_where} AND LOWER(tp.nombre) LIKE '%voluntariado%'
        GROUP BY p.proyecto_id,p.titulo,p.lugar_ejecucion,j.fecha,j.descripcion,ce.nombre,resp.responsable
        ORDER BY j.fecha DESC
        LIMIT 100
        """,
        params,
    )
    metrics = fetch_one(
        f"""
        SELECT COUNT(DISTINCT rp.persona_id) AS voluntarios_activos,
               COUNT(DISTINCT j.jornada_voluntaria_id) AS jornadas_registradas,
               ROUND(COALESCE(SUM(j.horas),0),1) AS horas_acumuladas,
               COUNT(DISTINCT p.proyecto_id) AS proyectos_voluntariado
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN rol_proyecto rp ON rp.proyecto_id=p.proyecto_id AND rp.activo=1
        LEFT JOIN jornada_voluntariado j ON j.rol_proyecto_id=rp.rol_proyecto_id
        {joins}
        WHERE {scope_where} AND LOWER(tp.nombre) LIKE '%voluntariado%'
        """,
        params,
    ) or {}
    trend = fetch_all(
        f"""
        SELECT DATE_FORMAT(j.fecha,'%Y-%m') AS periodo, ROUND(SUM(j.horas),1) AS total
        FROM jornada_voluntariado j
        INNER JOIN rol_proyecto rp ON rp.rol_proyecto_id=j.rol_proyecto_id
        INNER JOIN proyecto p ON p.proyecto_id=rp.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        {joins}
        WHERE {scope_where} AND LOWER(tp.nombre) LIKE '%voluntariado%'
          AND j.fecha >= DATE_SUB(CURDATE(), INTERVAL 11 MONTH)
        GROUP BY periodo ORDER BY periodo
        """,
        params,
    )
    faculty = fetch_all(
        f"""
        SELECT COALESCE(da.facultad,'Sin facultad') AS etiqueta,
               COUNT(DISTINCT rp.persona_id) AS cantidad
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN rol_proyecto rp ON rp.proyecto_id=p.proyecto_id AND rp.activo=1
        LEFT JOIN docente d ON d.persona_id=rp.persona_id
        LEFT JOIN estudiante e ON e.persona_id=rp.persona_id
        LEFT JOIN departamento_academico da ON da.departamento_academico_id=COALESCE(d.departamento_academico_id,e.departamento_academico_id)
        {joins}
        WHERE {scope_where} AND LOWER(tp.nombre) LIKE '%voluntariado%'
        GROUP BY COALESCE(da.facultad,'Sin facultad')
        ORDER BY cantidad DESC
        """,
        params,
    )
    upcoming = [row for row in journeys if row.get("fecha") and row.get("fecha") >= date.today()]
    upcoming.sort(key=lambda row: row.get("fecha"))
    return {
        "module": "voluntariado",
        "permissions": _permissions(user),
        "counts": {
            "voluntarios_activos": int(metrics.get("voluntarios_activos") or 0),
            "jornadas_registradas": int(metrics.get("jornadas_registradas") or 0),
            "horas_acumuladas": float(metrics.get("horas_acumuladas") or 0),
            "proyectos_voluntariado": int(metrics.get("proyectos_voluntariado") or 0),
        },
        "rows": journeys,
        "fallback_projects": project_rows,
        "status_distribution": _project_distribution(user, "estado", "voluntariado"),
        "secondary_distribution": faculty,
        "trend": trend,
        "side_items": upcoming[:8],
        "events": [event for event in _recent_events(user, 12) if event.get("referencia_id") in {row.get("proyecto_id") for row in project_rows}][:8],
    }


def _personal(user: AuthUser) -> dict[str, Any]:
    _require_admin(user)
    counts = fetch_one(
        """
        SELECT COUNT(*) AS personas,
               SUM(CASE WHEN d.persona_id IS NOT NULL THEN 1 ELSE 0 END) AS docentes,
               SUM(CASE WHEN e.persona_id IS NOT NULL THEN 1 ELSE 0 END) AS estudiantes,
               SUM(CASE WHEN a.persona_id IS NOT NULL THEN 1 ELSE 0 END) AS administrativos
        FROM persona p
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        """
    ) or {}
    rows = fetch_all(
        """
        SELECT p.persona_id, CONCAT(p.nombres,' ',p.apellidos) AS nombre,
               p.dni, p.activo, p.fecha_registro,
               CASE WHEN d.persona_id IS NOT NULL THEN 'Docente'
                    WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                    WHEN a.persona_id IS NOT NULL THEN 'Administrativo'
                    ELSE 'Persona' END AS rol,
               COALESCE(NULLIF(da.carrera,''),NULLIF(da.facultad,''),NULLIF(a.area,''),'—') AS dependencia,
               COALESCE(c.email_institucional,c.email_personal,'—') AS correo
        FROM persona p
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN departamento_academico da ON da.departamento_academico_id=COALESCE(d.departamento_academico_id,e.departamento_academico_id)
        ORDER BY p.fecha_registro DESC, p.apellidos, p.nombres
        LIMIT 1000
        """
    )
    type_distribution = [
        {"etiqueta": "Docentes", "cantidad": int(counts.get("docentes") or 0)},
        {"etiqueta": "Estudiantes", "cantidad": int(counts.get("estudiantes") or 0)},
        {"etiqueta": "Administrativos", "cantidad": int(counts.get("administrativos") or 0)},
    ]
    academic_areas = fetch_all(
        """
        SELECT etiqueta, SUM(cantidad) AS cantidad
        FROM (
            /* Incluye todas las carreras activas, incluso cuando todavía no tienen personal. */
            SELECT COALESCE(NULLIF(TRIM(da.carrera),''), NULLIF(TRIM(da.facultad),''), 'Sin área académica') AS etiqueta,
                   COUNT(DISTINCT academic_person.persona_id) AS cantidad
            FROM departamento_academico da
            LEFT JOIN (
                SELECT persona_id, departamento_academico_id FROM docente
                UNION ALL
                SELECT persona_id, departamento_academico_id FROM estudiante
            ) academic_person
              ON academic_person.departamento_academico_id=da.departamento_academico_id
            WHERE da.activo=1
            GROUP BY da.departamento_academico_id, etiqueta

            UNION ALL

            SELECT COALESCE(NULLIF(TRIM(a.area),''), 'Área administrativa') AS etiqueta,
                   COUNT(DISTINCT a.persona_id) AS cantidad
            FROM administrativo a
            GROUP BY etiqueta
        ) areas
        GROUP BY etiqueta
        ORDER BY cantidad DESC, etiqueta
        """
    )
    roles_by_person_type = fetch_all(
        """
        SELECT cr.nombre AS etiqueta,
               SUM(CASE WHEN d.persona_id IS NOT NULL THEN 1 ELSE 0 END) AS docentes,
               SUM(CASE WHEN e.persona_id IS NOT NULL THEN 1 ELSE 0 END) AS estudiantes,
               SUM(CASE WHEN a.persona_id IS NOT NULL THEN 1 ELSE 0 END) AS administrativos,
               COUNT(*) AS cantidad
        FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        INNER JOIN persona p ON p.persona_id=rp.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        WHERE rp.activo=1
        GROUP BY cr.rol_id, cr.nombre
        ORDER BY cantidad DESC, cr.nombre
        LIMIT 10
        """
    )
    recent = [
        {
            "tipo": "personal",
            "tipo_persona": str(row.get("rol") or "Estudiante").lower(),
            "titulo": "Persona registrada",
            "mensaje": f"{row.get('nombre')} · {row.get('rol')}",
            "fecha": row.get("fecha_registro"),
            "hora_registro": row.get("fecha_registro").strftime("%H:%M")
                if isinstance(row.get("fecha_registro"), datetime) else None,
            "referencia_id": row.get("persona_id"),
        }
        for row in rows[:10]
    ]
    return {
        "module": "personal",
        "permissions": _permissions(user),
        "counts": {
            "personas": int(counts.get("personas") or 0),
            "docentes": int(counts.get("docentes") or 0),
            "estudiantes": int(counts.get("estudiantes") or 0),
            "administrativos": int(counts.get("administrativos") or 0),
        },
        "rows": rows,
        "status_distribution": type_distribution,
        "secondary_distribution": academic_areas,
        "role_by_person_type": roles_by_person_type,
        "events": recent,
    }


def _evaluaciones(user: AuthUser) -> dict[str, Any]:
    _require_admin(user)
    summary = fetch_one(
        """
        SELECT
          (SELECT COUNT(*) FROM proyecto p INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
             WHERE LOWER(ce.nombre) IN ('presentado','en_evaluacion')) AS pendientes,
          (SELECT COUNT(*) FROM evaluacion_proyecto WHERE resultado='con_observaciones') AS observaciones,
          (SELECT COUNT(*) FROM evaluacion_proyecto WHERE resultado='aprobado') AS aprobados,
          (SELECT ROUND(AVG(GREATEST(DATEDIFF(ep.fecha_evaluacion,p.fecha_presentacion),0)),1)
             FROM evaluacion_proyecto ep INNER JOIN proyecto p ON p.proyecto_id=ep.proyecto_id
             WHERE p.fecha_presentacion IS NOT NULL) AS promedio_dias
        """
    ) or {}
    rows = fetch_all(
        """
        SELECT p.proyecto_id, p.titulo AS proyecto, resp.responsable,
               p.fecha_presentacion AS fecha_envio,
               COALESCE(eval.evaluador,'Sin asignar') AS revisor,
               ce.nombre AS estado,
               ep.fecha_limite_observaciones AS plazo_subsanacion
        FROM proyecto p
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        LEFT JOIN (
            SELECT rp.proyecto_id, CONCAT(pe.nombres,' ',pe.apellidos) AS responsable
            FROM rol_proyecto rp
            INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id AND LOWER(cr.nombre)='responsable'
            INNER JOIN persona pe ON pe.persona_id=rp.persona_id
            WHERE rp.activo=1
        ) resp ON resp.proyecto_id=p.proyecto_id
        LEFT JOIN (
            SELECT e1.proyecto_id, CONCAT(pe.nombres,' ',pe.apellidos) AS evaluador, e1.fecha_evaluacion
            FROM evaluacion_proyecto e1
            INNER JOIN persona pe ON pe.persona_id=e1.evaluador_persona_id
            INNER JOIN (SELECT proyecto_id, MAX(fecha_evaluacion) AS max_fecha FROM evaluacion_proyecto GROUP BY proyecto_id) latest
              ON latest.proyecto_id=e1.proyecto_id AND latest.max_fecha=e1.fecha_evaluacion
        ) eval ON eval.proyecto_id=p.proyecto_id
        LEFT JOIN etapa_proyecto ep ON ep.proyecto_id=p.proyecto_id AND LOWER(ep.nombre) LIKE '%evalu%'
        ORDER BY COALESCE(p.fecha_presentacion,p.fecha_creacion) DESC
        LIMIT 100
        """
    )
    results = fetch_all(
        """
        SELECT CASE WHEN resultado='con_observaciones' THEN 'Observado'
                    WHEN resultado='aprobado' THEN 'Aprobado'
                    ELSE resultado END AS etiqueta,
               COUNT(*) AS cantidad
        FROM evaluacion_proyecto
        GROUP BY resultado ORDER BY cantidad DESC
        """
    )
    trend = fetch_all(
        """
        SELECT DATE_FORMAT(ep.fecha_evaluacion,'%Y-%m') AS periodo,
               ROUND(AVG(GREATEST(DATEDIFF(ep.fecha_evaluacion,p.fecha_presentacion),0)),1) AS total
        FROM evaluacion_proyecto ep
        INNER JOIN proyecto p ON p.proyecto_id=ep.proyecto_id
        WHERE ep.fecha_evaluacion >= DATE_SUB(CURDATE(), INTERVAL 11 MONTH)
          AND p.fecha_presentacion IS NOT NULL
        GROUP BY periodo ORDER BY periodo
        """
    )
    recent = fetch_all(
        """
        SELECT 'evaluacion' AS tipo,
               CASE WHEN ep.resultado='aprobado' THEN 'Proyecto aprobado' ELSE 'Observaciones emitidas' END AS titulo,
               CONCAT(p.titulo,' · ',pe.nombres,' ',pe.apellidos) AS mensaje,
               ep.fecha_evaluacion AS fecha,
               p.proyecto_id AS referencia_id
        FROM evaluacion_proyecto ep
        INNER JOIN proyecto p ON p.proyecto_id=ep.proyecto_id
        INNER JOIN persona pe ON pe.persona_id=ep.evaluador_persona_id
        ORDER BY ep.fecha_evaluacion DESC LIMIT 10
        """
    )
    return {
        "module": "evaluaciones",
        "permissions": _permissions(user),
        "counts": {
            "pendientes": int(summary.get("pendientes") or 0),
            "observaciones": int(summary.get("observaciones") or 0),
            "aprobados": int(summary.get("aprobados") or 0),
            "promedio_dias": float(summary.get("promedio_dias") or 0),
        },
        "rows": rows,
        "status_distribution": results,
        "trend": trend,
        "events": recent,
    }


def _reportes(user: AuthUser) -> dict[str, Any]:
    _require_admin(user)
    summary = fetch_one(
        """
        SELECT
          (SELECT COUNT(*) FROM documento d INNER JOIN tipo_documento td ON td.tipo_documento_id=d.tipo_documento_id
             WHERE LOWER(td.nombre) LIKE '%informe%') AS reportes,
          (SELECT COUNT(*) FROM proyecto p INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
             WHERE LOWER(ce.nombre) NOT IN ('finalizado','rechazado','cancelado')) AS proyectos_activos,
          (SELECT COALESCE(SUM(horas_certificadas),0) FROM certificado) AS horas_certificadas,
          (SELECT COUNT(DISTINCT persona_id) FROM rol_proyecto WHERE activo=1) AS participantes
        """
    ) or {}
    rows = fetch_all(
        """
        SELECT d.documento_id, td.nombre AS reporte, d.fecha_subida AS fecha,
               d.nombre_archivo, CONCAT(pe.nombres,' ',pe.apellidos) AS generado_por,
               p.proyecto_id, p.titulo AS proyecto, ce.nombre AS estado
        FROM documento d
        INNER JOIN tipo_documento td ON td.tipo_documento_id=d.tipo_documento_id
        INNER JOIN etapa_proyecto et ON et.etapa_proyecto_id=d.etapa_proyecto_id
        INNER JOIN proyecto p ON p.proyecto_id=et.proyecto_id
        INNER JOIN persona pe ON pe.persona_id=d.subido_por
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=d.estado_id
        WHERE LOWER(td.nombre) LIKE '%informe%'
        ORDER BY d.fecha_subida DESC
        LIMIT 100
        """
    )
    projects_state = fetch_all(
        """
        SELECT ce.nombre AS etiqueta, COUNT(*) AS cantidad
        FROM proyecto p INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        GROUP BY ce.catalogo_estado_id,ce.nombre ORDER BY cantidad DESC
        """
    )
    participation = fetch_all(
        """
        SELECT COALESCE(da.facultad,'Sin facultad') AS etiqueta,
               COUNT(DISTINCT rp.persona_id) AS cantidad
        FROM rol_proyecto rp
        LEFT JOIN docente d ON d.persona_id=rp.persona_id
        LEFT JOIN estudiante e ON e.persona_id=rp.persona_id
        LEFT JOIN departamento_academico da ON da.departamento_academico_id=COALESCE(d.departamento_academico_id,e.departamento_academico_id)
        WHERE rp.activo=1
        GROUP BY COALESCE(da.facultad,'Sin facultad')
        ORDER BY cantidad DESC
        """
    )
    hours_modality = fetch_all(
        """
        SELECT tp.nombre AS etiqueta, COALESCE(SUM(c.horas_certificadas),0) AS cantidad
        FROM certificado c
        INNER JOIN proyecto p ON p.proyecto_id=c.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        GROUP BY tp.tipo_proyecto_id,tp.nombre ORDER BY cantidad DESC
        """
    )
    trend = fetch_all(
        """
        SELECT DATE_FORMAT(fecha_emision,'%Y-%m') AS periodo, SUM(horas_certificadas) AS total
        FROM certificado
        WHERE fecha_emision IS NOT NULL AND fecha_emision >= DATE_SUB(CURDATE(), INTERVAL 11 MONTH)
        GROUP BY periodo ORDER BY periodo
        """
    )
    recent = [
        {
            "tipo": "reporte",
            "titulo": row.get("reporte"),
            "mensaje": row.get("proyecto"),
            "fecha": row.get("fecha"),
            "referencia_id": row.get("documento_id"),
        }
        for row in rows[:10]
    ]
    return {
        "module": "reportes",
        "permissions": _permissions(user),
        "counts": {
            "reportes": int(summary.get("reportes") or 0),
            "proyectos_activos": int(summary.get("proyectos_activos") or 0),
            "horas_certificadas": int(summary.get("horas_certificadas") or 0),
            "participantes": int(summary.get("participantes") or 0),
        },
        "rows": rows,
        "status_distribution": projects_state,
        "secondary_distribution": participation,
        "tertiary_distribution": hours_modality,
        "trend": trend,
        "events": recent,
    }


def _notificaciones(user: AuthUser) -> dict[str, Any]:
    events = _recent_events(user, 60)
    today = date.today()
    today_count = 0
    week_count = 0
    type_counts: dict[str, int] = {}
    for event in events:
        value = event.get("fecha")
        event_date = value.date() if isinstance(value, datetime) else value
        if event_date == today:
            today_count += 1
        if event_date and (today - event_date).days <= 7:
            week_count += 1
        key = str(event.get("tipo") or "sistema").capitalize()
        type_counts[key] = type_counts.get(key, 0) + 1
    certificates = sum(1 for event in events if event.get("tipo") == "certificado")
    return {
        "module": "notificaciones",
        "permissions": _permissions(user),
        "counts": {
            "eventos_recientes": len(events),
            "hoy": today_count,
            "semana": week_count,
            "certificados": certificates,
        },
        "rows": events,
        "status_distribution": [{"etiqueta": key, "cantidad": value} for key, value in type_counts.items()],
        "events": events[:12],
        "meta": {"persistent_notifications": False},
    }


_BUILDERS: dict[str, Callable[[AuthUser], dict[str, Any]]] = {
    "inicio": _inicio,
    "convocatorias": _convocatorias,
    "proyectos": _proyectos,
    "actividades": _actividades,
    "voluntariado": _voluntariado,
    "personal": _personal,
    "evaluaciones": _evaluaciones,
    "reportes": _reportes,
    "notificaciones": _notificaciones,
}


def build_module_payload(module_key: str, user: AuthUser) -> dict[str, Any]:
    key = module_key.strip().lower()
    builder = _BUILDERS.get(key)
    if builder is None:
        raise HTTPException(status_code=404, detail="Módulo de interfaz no encontrado.")
    return builder(user)
