from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.db.mysql import fetch_all, fetch_one
from app.schemas.auth import AuthUser
from app.services.dashboard_period_filter import convocation_period_filter, project_period_filter
from app.services.rsu_hours import calculate_rsu_person_hours


_GLOBAL_ROLES = {
    "administrador_rsu",
    "director_rsu",
    "vicepresidente_academico",
}


def _role(user: AuthUser) -> str:
    return str(user.rol_sistema or "").strip().lower()


def _is_global(user: AuthUser) -> bool:
    role = _role(user)
    if role:
        return role in _GLOBAL_ROLES
    return str(user.tipo_usuario or "").strip().lower() in {
        "administrativo",
        "administrador",
        "admin",
    }


def _periods() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT periodo_academico_id, nombre, fecha_inicio, fecha_fin, activo
        FROM periodo_academico
        ORDER BY fecha_inicio DESC, nombre DESC
        """
    )


def _resolve_period(period_id: str | None) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]]]:
    periods = _periods()
    raw = str(period_id or "").strip()
    if raw.lower() in {"all", "todos", "*"}:
        return None, None, periods
    if raw:
        selected = next((row for row in periods if str(row.get("periodo_academico_id")) == raw), None)
        if selected:
            return raw, selected, periods
    selected = next((row for row in periods if bool(row.get("activo"))), None)
    if not selected and periods:
        selected = periods[0]
    return (
        str(selected.get("periodo_academico_id")) if selected else None,
        selected,
        periods,
    )


def _scope_sql(user: AuthUser, project_alias: str = "p") -> tuple[str, list[Any]]:
    if _is_global(user):
        return "1=1", []
    role = _role(user)
    if role == "director_facultad":
        return (
            f"EXISTS (SELECT 1 FROM rsu_project_review_assignment pra_scope "
            f"WHERE pra_scope.proyecto_id={project_alias}.proyecto_id "
            "AND pra_scope.revisor_persona_id=%s AND pra_scope.activo=1)",
            [user.persona_id],
        )
    return (
        f"EXISTS (SELECT 1 FROM rol_proyecto rp_scope "
        f"WHERE rp_scope.proyecto_id={project_alias}.proyecto_id "
        "AND rp_scope.persona_id=%s AND rp_scope.activo=1)",
        [user.persona_id],
    )


def _period_sql(period_id: str | None, project_alias: str = "p", call_alias: str = "cv") -> tuple[str, list[Any]]:
    return project_period_filter(period_id, project_alias, call_alias)


def _where(user: AuthUser, period_id: str | None, project_alias: str = "p", call_alias: str = "cv") -> tuple[str, tuple[Any, ...]]:
    scope_clause, scope_values = _scope_sql(user, project_alias)
    period_clause, period_values = _period_sql(period_id, project_alias, call_alias)
    return f"({scope_clause}) AND ({period_clause})", tuple(scope_values + period_values)


def _state_key_sql(alias: str = "ce") -> str:
    return f"LOWER(REPLACE(REPLACE(COALESCE({alias}.nombre,''),' ', '_'),'ó','o'))"


def _module_case_sql(type_alias: str = "tp") -> str:
    return (
        f"CASE WHEN LOWER({type_alias}.nombre) LIKE '%volunt%' THEN 'voluntariado' "
        f"WHEN LOWER({type_alias}.nombre) LIKE '%actividades espec%' "
        f"  OR LOWER({type_alias}.nombre) LIKE '%proyeccion social%' THEN 'actividades' "
        "ELSE 'proyectos' END"
    )


def _summary(user: AuthUser, period_id: str | None) -> dict[str, int]:
    where, params = _where(user, period_id)
    state_key = _state_key_sql()
    row = fetch_one(
        f"""
        SELECT COUNT(DISTINCT p.proyecto_id) AS total_expedientes,
               COUNT(DISTINCT CASE
                   WHEN {state_key} LIKE '%evaluacion%' OR {state_key} IN ('presentado','actividad_presentada')
                   THEN p.proyecto_id END) AS en_evaluacion,
               COUNT(DISTINCT CASE WHEN {state_key} LIKE '%ejecucion%' THEN p.proyecto_id END) AS en_ejecucion,
               COUNT(DISTINCT CASE WHEN {state_key} LIKE '%observ%' THEN p.proyecto_id END) AS observados
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        WHERE {where}
        """,
        params,
    ) or {}

    certificates = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM certificado c
        INNER JOIN proyecto p ON p.proyecto_id=c.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        WHERE c.fecha_emision IS NOT NULL AND {where}
        """,
        params,
    ) or {"total": 0}

    # Horas-persona realmente realizadas por responsables y participantes de
    # Proyectos RSU, Actividades específicas y Voluntariado. Se priorizan las
    # horas certificadas, jornadas e informes finales antes del dato histórico.
    hours_total = calculate_rsu_person_hours(where, params)

    return {
        "total_expedientes": int(row.get("total_expedientes") or 0),
        "en_evaluacion": int(row.get("en_evaluacion") or 0),
        "en_ejecucion": int(row.get("en_ejecucion") or 0),
        "observados": int(row.get("observados") or 0),
        "certificados_emitidos": int(certificates.get("total") or 0),
        "horas_rsu_realizadas": float(hours_total or 0),
    }


def _module_summaries(user: AuthUser, period_id: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id)
    state_key = _state_key_sql()
    module_case = _module_case_sql()
    rows = fetch_all(
        f"""
        SELECT {module_case} AS modulo,
               COUNT(DISTINCT p.proyecto_id) AS total,
               COUNT(DISTINCT CASE
                   WHEN {state_key} LIKE '%ejecucion%' OR {state_key} LIKE '%aprobado%'
                        OR {state_key} LIKE '%memorandum%' OR {state_key} LIKE '%certific%'
                   THEN p.proyecto_id END) AS activos,
               COUNT(DISTINCT CASE
                   WHEN {state_key} LIKE '%evaluacion%' OR {state_key} LIKE '%observ%'
                        OR LOCATE('subsan', {state_key}) > 0 OR {state_key} IN ('presentado','actividad_presentada')
                   THEN p.proyecto_id END) AS pendientes,
               COUNT(DISTINCT CASE WHEN {state_key} LIKE '%ejecucion%' THEN p.proyecto_id END) AS ejecucion
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        WHERE {where}
        GROUP BY modulo
        """,
        params,
    )
    by_key = {str(row.get("modulo")): row for row in rows}

    call_period, call_values = convocation_period_filter(period_id, "cv")
    call_params: tuple[Any, ...] = tuple(call_values)
    calls = fetch_one(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN LOWER(ce.nombre)='abierta'
                         AND cv.fecha_inicio<=CURDATE()
                         AND cv.fecha_limite_presentacion>=CURDATE() THEN 1 ELSE 0 END) AS vigentes,
               SUM(CASE WHEN cv.fecha_limite_presentacion BETWEEN CURDATE() AND DATE_ADD(CURDATE(),INTERVAL 15 DAY)
                        THEN 1 ELSE 0 END) AS proximas_cerrar
        FROM convocatoria cv
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        WHERE {call_period}
        """,
        call_params,
    ) or {}

    specs = [
        ("proyectos", "Proyectos RSU", "Activos", "Pendientes", "folder", "#1677E8"),
        ("actividades", "Actividades específicas", "Activas", "En revisión", "activities", "#7758E8"),
        ("voluntariado", "Voluntariado", "Registros", "En ejecución", "volunteer", "#24A148"),
    ]
    result: list[dict[str, Any]] = []
    for key, title, primary_label, secondary_label, icon, tone in specs:
        row = by_key.get(key, {})
        primary_value = int(row.get("total") or 0) if key == "voluntariado" else int(row.get("activos") or 0)
        secondary_value = int(row.get("ejecucion") or 0) if key == "voluntariado" else int(row.get("pendientes") or 0)
        result.append(
            {
                "key": key,
                "title": title,
                "icon": icon,
                "tone": tone,
                "primary_label": primary_label,
                "primary_value": primary_value,
                "secondary_label": secondary_label,
                "secondary_value": secondary_value,
                "total": int(row.get("total") or 0),
            }
        )
    result.append(
        {
            "key": "convocatorias",
            "title": "Convocatorias",
            "icon": "megaphone",
            "tone": "#F59E0B",
            "primary_label": "Vigentes",
            "primary_value": int(calls.get("vigentes") or 0),
            "secondary_label": "Próximas a cerrar",
            "secondary_value": int(calls.get("proximas_cerrar") or 0),
            "total": int(calls.get("total") or 0),
        }
    )
    return result


def _module_distribution(user: AuthUser, period_id: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id)
    rows = fetch_all(
        f"""
        SELECT {_module_case_sql()} AS modulo, COUNT(DISTINCT p.proyecto_id) AS cantidad
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        WHERE {where}
        GROUP BY modulo
        """,
        params,
    )
    labels = {
        "proyectos": "Proyectos RSU",
        "actividades": "Actividades específicas",
        "voluntariado": "Voluntariado",
    }
    by_key = {str(row.get("modulo")): int(row.get("cantidad") or 0) for row in rows}
    return [{"etiqueta": labels[key], "cantidad": by_key.get(key, 0)} for key in labels]


def _trend(user: AuthUser, period_id: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id)
    return fetch_all(
        f"""
        SELECT DATE_FORMAT(COALESCE(p.fecha_presentacion,p.fecha_creacion),'%Y-%m') AS periodo,
               COUNT(DISTINCT p.proyecto_id) AS cantidad
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        WHERE {where}
          AND COALESCE(p.fecha_presentacion,p.fecha_creacion) IS NOT NULL
        GROUP BY periodo
        ORDER BY periodo
        """,
        params,
    )


def _state_distribution(user: AuthUser, period_id: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id)
    state_key = _state_key_sql()
    return fetch_all(
        f"""
        SELECT CASE
                 WHEN {state_key} LIKE '%observ%' THEN 'Observados'
                 WHEN LOCATE('subsan', {state_key}) > 0 THEN 'En subsanación'
                 WHEN {state_key} LIKE '%aprob%' OR {state_key} LIKE '%ejecucion%'
                      OR {state_key} LIKE '%memorandum%' OR {state_key} LIKE '%certific%'
                      OR {state_key} LIKE '%finaliz%' THEN 'Aprobados'
                 ELSE 'En evaluación'
               END AS etiqueta,
               COUNT(DISTINCT p.proyecto_id) AS cantidad
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        WHERE {where}
        GROUP BY etiqueta
        ORDER BY FIELD(etiqueta,'Aprobados','En evaluación','Observados','En subsanación')
        """,
        params,
    )


def _pending(user: AuthUser, period_id: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id)
    role = _role(user)
    values = list(params)
    if _is_global(user):
        assignment_filter = "pra.activo=1 AND pra.estado='pendiente'"
    elif role == "director_facultad":
        assignment_filter = "pra.activo=1 AND pra.estado='pendiente' AND pra.revisor_persona_id=%s"
        values.append(user.persona_id)
    else:
        assignment_filter = "0=1"
    review_rows = fetch_all(
        f"""
        SELECT pra.assignment_id AS referencia_id,
               CONCAT('Revisar Anexo ',pra.anexo_numero) AS titulo,
               p.titulo AS subtitulo,
               CASE WHEN LOWER(tp.nombre) LIKE '%volunt%' THEN 'Voluntariado'
                    WHEN LOWER(tp.nombre) LIKE '%actividades espec%' THEN 'Actividad específica'
                    ELSE 'Proyecto RSU' END AS modulo,
               'En evaluación' AS estado,
               pra.fecha_asignacion AS fecha
        FROM rsu_project_review_assignment pra
        INNER JOIN proyecto p ON p.proyecto_id=pra.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        WHERE {where} AND {assignment_filter}
        ORDER BY pra.fecha_asignacion ASC
        LIMIT 5
        """,
        tuple(values),
    )
    if review_rows:
        return review_rows

    # Para responsables y colaboradores se muestran las subsanaciones o fichas pendientes.
    scope, scope_values = _scope_sql(user, "p")
    period_clause, period_values = _period_sql(period_id, "p", "cv")
    return fetch_all(
        f"""
        SELECT p.proyecto_id AS referencia_id,
               CASE WHEN LOWER(ce.nombre) LIKE '%observ%' THEN 'Subsanación de observaciones'
                    ELSE 'Completar expediente' END AS titulo,
               p.titulo AS subtitulo,
               CASE WHEN LOWER(tp.nombre) LIKE '%volunt%' THEN 'Voluntariado'
                    WHEN LOWER(tp.nombre) LIKE '%actividades espec%' THEN 'Actividad específica'
                    ELSE 'Proyecto RSU' END AS modulo,
               ce.nombre AS estado,
               COALESCE(p.fecha_presentacion,p.fecha_creacion) AS fecha
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        WHERE ({scope}) AND ({period_clause})
          AND (LOWER(ce.nombre) LIKE '%observ%' OR LOWER(ce.nombre) LIKE '%pendiente%')
        ORDER BY COALESCE(p.fecha_presentacion,p.fecha_creacion) DESC
        LIMIT 5
        """,
        tuple(scope_values + period_values),
    )


def _responsible_name_sql(project_alias: str = "p") -> str:
    return (
        "COALESCE((SELECT CONCAT(pe.nombres,' ',pe.apellidos) "
        "FROM rol_proyecto rp INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id "
        "INNER JOIN persona pe ON pe.persona_id=rp.persona_id "
        f"WHERE rp.proyecto_id={project_alias}.proyecto_id AND rp.activo=1 "
        "AND LOWER(cr.nombre)='responsable' LIMIT 1),'Sin responsable')"
    )


def _deadlines(user: AuthUser, period_id: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id)
    responsible = _responsible_name_sql("p")
    values: list[Any] = []
    parts: list[str] = []
    for table_name, date_column, label in (
        ("rsu_project_workflow", "fecha_limite_actual", "Plazo del proyecto RSU"),
        ("rsu_specific_activity_workflow", "fecha_limite_subsanacion", "Subsanación de actividad"),
        ("rsu_volunteer_workflow", "fecha_limite_subsanacion", "Subsanación de voluntariado"),
    ):
        parts.append(
            f"""
            SELECT p.proyecto_id AS referencia_id, '{label}' AS proceso,
                   p.titulo, {responsible} AS responsable,
                   wf.{date_column} AS fecha
            FROM {table_name} wf
            INNER JOIN proyecto p ON p.proyecto_id=wf.proyecto_id
            INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
            WHERE {where} AND wf.{date_column} IS NOT NULL
            """
        )
        values.extend(params)
    return fetch_all(
        "SELECT * FROM (" + " UNION ALL ".join(parts) + ") vencimientos "
        "WHERE fecha>=CURDATE() ORDER BY fecha ASC LIMIT 6",
        tuple(values),
    )


def _quick_indicators(user: AuthUser, period_id: str | None) -> list[dict[str, Any]]:
    # Los usuarios y las autoridades son indicadores institucionales. El número
    # de responsables sí respeta el ciclo y el alcance del usuario autenticado.
    users = fetch_one("SELECT COUNT(*) AS total FROM usuario WHERE activo=1") or {}
    # rsu_user_system_role relaciona los roles mediante usuario_id. La persona
    # se obtiene a través de usuario; la tabla de asignaciones no posee persona_id.
    admins = fetch_one(
        """
        SELECT COUNT(DISTINCT u.persona_id) AS total
        FROM rsu_user_system_role usr
        INNER JOIN usuario u ON u.usuario_id=usr.usuario_id
        INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id
        WHERE usr.activo=1 AND u.activo=1 AND sr.activo=1
          AND sr.codigo IN ('administrador_rsu','director_rsu')
        """
    ) or {}
    directors = fetch_one(
        """
        SELECT COUNT(DISTINCT u.persona_id) AS total
        FROM rsu_user_system_role usr
        INNER JOIN usuario u ON u.usuario_id=usr.usuario_id
        INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id
        WHERE usr.activo=1 AND u.activo=1 AND sr.activo=1
          AND sr.codigo='director_facultad'
        """
    ) or {}
    where, params = _where(user, period_id)
    responsibles = fetch_one(
        f"""
        SELECT COUNT(DISTINCT rp.persona_id) AS total
        FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        INNER JOIN proyecto p ON p.proyecto_id=rp.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        WHERE rp.activo=1 AND LOWER(cr.nombre)='responsable' AND {where}
        """,
        params,
    ) or {}
    return [
        {"key": "usuarios", "label": "Usuarios registrados", "value": int(users.get("total") or 0), "icon": "users", "tone": "#1677E8"},
        {"key": "administradores", "label": "Administradores RSU", "value": int(admins.get("total") or 0), "icon": "settings", "tone": "#7758E8"},
        {"key": "directores", "label": "Directores de facultad", "value": int(directors.get("total") or 0), "icon": "building", "tone": "#12A6B5"},
        {"key": "responsables", "label": "Responsables activos", "value": int(responsibles.get("total") or 0), "icon": "check", "tone": "#24A148"},
    ]


def _recent_activity(user: AuthUser, period_id: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id)
    actor = "COALESCE(CONCAT(pe.nombres,' ',pe.apellidos),'Sistema RSU')"
    values: list[Any] = []
    parts: list[str] = []

    parts.append(
        f"""
        SELECT 'flujo' AS tipo,
               CASE WHEN LOWER(COALESCE(h.estado_nuevo,'')) LIKE '%observ%' THEN 'Observación registrada'
                    WHEN LOWER(COALESCE(h.estado_nuevo,'')) LIKE '%aprob%' THEN 'Expediente aprobado'
                    ELSE 'Actualización de expediente' END AS titulo,
               COALESCE(h.comentario,p.titulo) AS mensaje,
               h.fecha_cambio AS fecha,
               p.proyecto_id AS referencia_id,
               {actor} AS responsable
        FROM rsu_workflow_history h
        INNER JOIN proyecto p ON p.proyecto_id=h.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        LEFT JOIN persona pe ON pe.persona_id=h.cambiado_por
        WHERE {where}
        """
    )
    values.extend(params)

    for table_name, title in (
        ("rsu_project_memorandum", "Memorándum emitido"),
        ("rsu_specific_activity_memorandum", "Memorándum de actividad emitido"),
        ("rsu_volunteer_memorandum", "Memorándum de voluntariado emitido"),
    ):
        parts.append(
            f"""
            SELECT 'memorandum' AS tipo, '{title}' AS titulo,
                   CONCAT(COALESCE(m.numero_memorandum,'Sin número'),' · ',p.titulo) AS mensaje,
                   m.fecha_registro AS fecha, p.proyecto_id AS referencia_id,
                   {actor} AS responsable
            FROM {table_name} m
            INNER JOIN proyecto p ON p.proyecto_id=m.proyecto_id
            INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
            LEFT JOIN persona pe ON pe.persona_id=m.emitido_por
            WHERE {where}
            """
        )
        values.extend(params)

    parts.append(
        f"""
        SELECT 'certificado' AS tipo, 'Certificado emitido' AS titulo,
               CONCAT(p.titulo,' · ',benef.nombres,' ',benef.apellidos) AS mensaje,
               c.fecha_emision AS fecha, p.proyecto_id AS referencia_id,
               CONCAT(benef.nombres,' ',benef.apellidos) AS responsable
        FROM certificado c
        INNER JOIN proyecto p ON p.proyecto_id=c.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN persona benef ON benef.persona_id=c.persona_id
        WHERE c.fecha_emision IS NOT NULL AND {where}
        """
    )
    values.extend(params)

    return fetch_all(
        "SELECT * FROM (" + " UNION ALL ".join(parts) + ") actividad "
        "WHERE fecha IS NOT NULL ORDER BY fecha DESC LIMIT 8",
        tuple(values),
    )


def build_general_dashboard(user: AuthUser, period_id: str | None = None) -> dict[str, Any]:
    selected_id, selected_period, periods = _resolve_period(period_id)
    summary = _summary(user, selected_id)
    module_summaries = _module_summaries(user, selected_id)
    distribution = _module_distribution(user, selected_id)
    state_distribution = _state_distribution(user, selected_id)
    return {
        "module": "inicio",
        "title": "Dashboard General",
        "subtitle": "Resumen ejecutivo de toda la aplicación",
        "selected_period_id": selected_id,
        "selected_period": selected_period,
        "periods": periods,
        "counts": summary,
        "module_summaries": module_summaries,
        "module_distribution": distribution,
        "trend": _trend(user, selected_id),
        "state_distribution": state_distribution,
        "pending": _pending(user, selected_id),
        "deadlines": _deadlines(user, selected_id),
        "quick_indicators": _quick_indicators(user, selected_id),
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "scope": "institucional" if _is_global(user) else "asignado_al_usuario",
            "cycle_filter_applied": bool(selected_id),
        },
    }
