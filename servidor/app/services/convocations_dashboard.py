from __future__ import annotations

from datetime import date
from typing import Any

from app.db.mysql import fetch_all, fetch_one
from app.schemas.auth import AuthUser
from app.services.dashboard_period_filter import convocation_period_filter


_TYPE_OPTIONS = [
    {"key": "all", "name": "Todas"},
    {"key": "proyectos", "name": "Proyectos RSU"},
    {"key": "actividades", "name": "Actividades específicas"},
    {"key": "voluntariado", "name": "Voluntariado"},
]

# Los perfiles de administración RSU conservan la lectura institucional. El
# resto de usuarios obtiene exclusivamente convocatorias vinculadas a su
# participación, facultad, evaluaciones o memorándums recibidos.
_GENERAL_ROLES = {"administrador_rsu", "director_rsu"}


def _role(user: AuthUser) -> str:
    return str(user.rol_sistema or "").strip().lower()


def _is_general_view(user: AuthUser) -> bool:
    role = _role(user)
    if role:
        return role in _GENERAL_ROLES
    return str(user.tipo_usuario or "").strip().lower() in {
        "administrativo", "administrador", "admin"
    }


def _periods() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT periodo_academico_id, nombre, fecha_inicio, fecha_fin, activo
        FROM periodo_academico
        ORDER BY fecha_inicio DESC, nombre DESC
        """
    )


def _resolve_period(value: str | None) -> tuple[str | None, list[dict[str, Any]]]:
    periods = _periods()
    raw = str(value or "").strip()
    if raw.lower() in {"all", "todos", "*"}:
        return None, periods
    if raw and any(str(row.get("periodo_academico_id")) == raw for row in periods):
        return raw, periods
    active = next((row for row in periods if bool(row.get("activo"))), None)
    if active is None and periods:
        active = periods[0]
    return (str(active.get("periodo_academico_id")) if active else None), periods


def _profile_scope(user: AuthUser) -> tuple[str, list[Any]]:
    """Devuelve el alcance que nunca puede ampliarse mediante los filtros UI."""
    if _is_general_view(user):
        return "1=1", []

    role = _role(user)
    person_id = str(user.persona_id)
    if role == "director_facultad":
        assigned = str(user.facultad_asignada or "").strip()
        if assigned:
            return "COALESCE(NULLIF(TRIM(rf.facultad),''),'Sin facultad')=%s", [assigned]
        return (
            "EXISTS (SELECT 1 FROM rsu_project_review_assignment pra_profile "
            "WHERE pra_profile.proyecto_id=p.proyecto_id "
            "AND pra_profile.revisor_persona_id=%s AND pra_profile.activo=1)",
            [person_id],
        )

    if role == "vicepresidente_academico":
        return (
            "(EXISTS (SELECT 1 FROM rsu_project_memorandum pm_profile "
            "WHERE pm_profile.proyecto_id=p.proyecto_id AND pm_profile.vicepresidente_persona_id=%s) "
            "OR EXISTS (SELECT 1 FROM rsu_certificate_memorandum cm_profile "
            "WHERE cm_profile.proyecto_id=p.proyecto_id AND cm_profile.vicepresidente_persona_id=%s) "
            "OR EXISTS (SELECT 1 FROM rsu_specific_activity_memorandum sm_profile "
            "WHERE sm_profile.proyecto_id=p.proyecto_id AND sm_profile.vicepresidente_persona_id=%s) "
            "OR EXISTS (SELECT 1 FROM rsu_volunteer_memorandum vm_profile "
            "WHERE vm_profile.proyecto_id=p.proyecto_id AND vm_profile.vicepresidente_persona_id=%s))",
            [person_id, person_id, person_id, person_id],
        )

    return (
        "EXISTS (SELECT 1 FROM rol_proyecto rp_profile "
        "WHERE rp_profile.proyecto_id=p.proyecto_id "
        "AND rp_profile.persona_id=%s AND rp_profile.activo=1)",
        [person_id],
    )


def _faculties(user: AuthUser) -> list[str]:
    if _is_general_view(user):
        rows = fetch_all(
            """
            SELECT DISTINCT TRIM(facultad) AS facultad
            FROM departamento_academico
            WHERE activo=1 AND TRIM(COALESCE(facultad,''))<>''
            ORDER BY facultad
            """
        )
    else:
        scope, params = _profile_scope(user)
        rows = fetch_all(
            f"""
            SELECT DISTINCT TRIM(rf.facultad) AS facultad
            FROM convocatoria cv
            INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
            INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
            {_project_context_join()}
            WHERE p.proyecto_id IS NOT NULL
              AND TRIM(COALESCE(rf.facultad,''))<>''
              AND ({scope})
            ORDER BY facultad
            """,
            tuple(params),
        )
    return [str(row.get("facultad")) for row in rows if row.get("facultad")]


def _resolve_faculty(user: AuthUser, value: str | None, faculties: list[str]) -> str | None:
    assigned = str(user.facultad_asignada or "").strip()
    if _role(user) == "director_facultad" and assigned:
        return assigned
    raw = str(value or "").strip()
    if raw.lower() in {"", "all", "todos", "*"}:
        return None
    return raw if raw in faculties else None


def _resolve_type(value: str | None) -> str | None:
    raw = str(value or "").strip().lower()
    aliases = {
        "proyecto": "proyectos",
        "proyectos": "proyectos",
        "proyectos_rsu": "proyectos",
        "actividad": "actividades",
        "actividades": "actividades",
        "actividades_especificas": "actividades",
        "voluntario": "voluntariado",
        "voluntariado": "voluntariado",
    }
    if raw in {"", "all", "todos", "*"}:
        return None
    return aliases.get(raw)


def _type_case() -> str:
    return """
        CASE
          WHEN LOWER(tp.nombre) LIKE '%%volunt%%' THEN 'voluntariado'
          WHEN LOWER(tp.nombre) LIKE '%%actividades espec%%'
            OR LOWER(tp.nombre) LIKE '%%proyeccion social%%'
            OR LOWER(tp.nombre) LIKE '%%proyección social%%' THEN 'actividades'
          ELSE 'proyectos'
        END
    """


def _project_context_join() -> str:
    return """
        LEFT JOIN proyecto p ON p.convocatoria_id=cv.convocatoria_id
        LEFT JOIN catalogo_estado pce ON pce.catalogo_estado_id=p.estado_id
        LEFT JOIN (
            SELECT rp_fac.proyecto_id,
                   MAX(COALESCE(dd.facultad,de.facultad,usr.facultad,'')) AS facultad,
                   MAX(CONCAT(pe.nombres,' ',pe.apellidos)) AS responsable
            FROM rol_proyecto rp_fac
            INNER JOIN catalogo_rol cr_fac ON cr_fac.rol_id=rp_fac.rol_id
            INNER JOIN persona pe ON pe.persona_id=rp_fac.persona_id
            LEFT JOIN docente doc ON doc.persona_id=rp_fac.persona_id
            LEFT JOIN estudiante est ON est.persona_id=rp_fac.persona_id
            LEFT JOIN departamento_academico dd ON dd.departamento_academico_id=doc.departamento_academico_id
            LEFT JOIN departamento_academico de ON de.departamento_academico_id=est.departamento_academico_id
            LEFT JOIN usuario u_fac ON u_fac.persona_id=rp_fac.persona_id
            LEFT JOIN rsu_user_system_role usr ON usr.usuario_id=u_fac.usuario_id AND usr.activo=1
            WHERE rp_fac.activo=1 AND LOWER(cr_fac.nombre)='responsable'
            GROUP BY rp_fac.proyecto_id
        ) rf ON rf.proyecto_id=p.proyecto_id
    """


def _where(user: AuthUser, period_id: str | None, faculty: str | None, call_type: str | None) -> tuple[str, tuple[Any, ...]]:
    scope_clause, scope_values = _profile_scope(user)
    period_clause, period_values = convocation_period_filter(period_id, "cv")
    clauses = [scope_clause, period_clause]
    params: list[Any] = list(scope_values) + list(period_values)
    if call_type:
        clauses.append(f"({_type_case()})=%s")
        params.append(call_type)
    if faculty:
        clauses.append("COALESCE(NULLIF(TRIM(rf.facultad),''),'Sin facultad')=%s")
        params.append(faculty)
    return " AND ".join(f"({clause})" for clause in clauses), tuple(params)


def _percentage(value: Any, total: Any) -> int:
    try:
        numerator = float(value or 0)
        denominator = float(total or 0)
    except (TypeError, ValueError):
        return 0
    if denominator <= 0:
        return 0
    return max(0, min(100, round(numerator / denominator * 100)))


def _summary(user: AuthUser, period_id: str | None, faculty: str | None, call_type: str | None) -> dict[str, int]:
    where, params = _where(user, period_id, faculty, call_type)
    state = "LOWER(COALESCE(ce.nombre,''))"
    row = fetch_one(
        f"""
        SELECT COUNT(DISTINCT cv.convocatoria_id) AS registradas,
               COUNT(DISTINCT CASE
                 WHEN {state}='abierta' AND cv.fecha_inicio<=CURDATE()
                  AND cv.fecha_limite_presentacion>=CURDATE() THEN cv.convocatoria_id END) AS vigentes,
               COUNT(DISTINCT CASE
                 WHEN {state}<>'cerrada' AND cv.fecha_limite_presentacion BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 14 DAY)
                 THEN cv.convocatoria_id END) AS proximas,
               COUNT(DISTINCT CASE
                 WHEN {state}='cerrada' OR cv.fecha_limite_presentacion<CURDATE() THEN cv.convocatoria_id END) AS cerradas,
               COUNT(DISTINCT p.proyecto_id) AS postulaciones,
               COUNT(DISTINCT NULLIF(TRIM(rf.facultad),'')) AS facultades
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        {_project_context_join()}
        WHERE {where}
        """,
        params,
    ) or {}
    return {key: int(row.get(key) or 0) for key in ("registradas", "vigentes", "proximas", "cerradas", "postulaciones", "facultades")}


def _module_summaries(user: AuthUser, period_id: str | None, faculty: str | None, call_type: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id, faculty, call_type)
    state = "LOWER(COALESCE(ce.nombre,''))"
    project_state = "LOWER(COALESCE(pce.nombre,''))"
    rows = fetch_all(
        f"""
        SELECT ({_type_case()}) AS modulo,
               COUNT(DISTINCT cv.convocatoria_id) AS total,
               COUNT(DISTINCT CASE WHEN {state}='abierta' AND cv.fecha_inicio<=CURDATE()
                    AND cv.fecha_limite_presentacion>=CURDATE() THEN cv.convocatoria_id END) AS aperturadas,
               COUNT(DISTINCT CASE WHEN {project_state} LIKE '%%evalu%%'
                    OR {project_state} LIKE '%%revision%%' OR {project_state} LIKE '%%pendiente%%'
                    THEN cv.convocatoria_id END) AS en_evaluacion,
               COUNT(DISTINCT CASE WHEN LOWER(COALESCE(pa.nombre,'')) LIKE '%%anual%%'
                    THEN cv.convocatoria_id END) AS permanentes,
               COUNT(DISTINCT CASE WHEN {state}='cerrada' OR cv.fecha_limite_presentacion<CURDATE()
                    THEN cv.convocatoria_id END) AS cerradas,
               COUNT(DISTINCT CASE WHEN {state}<>'cerrada'
                    AND cv.fecha_limite_presentacion BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 14 DAY)
                    THEN cv.convocatoria_id END) AS proximas
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        {_project_context_join()}
        WHERE {where}
        GROUP BY modulo
        """,
        params,
    )
    indexed = {str(row.get("modulo")): row for row in rows}

    def item(module: str, title: str, icon: str, tone: str, second_label: str, second_key: str) -> dict[str, Any]:
        row = indexed.get(module, {})
        total = int(row.get("total") or 0)
        opened = int(row.get("aperturadas") or 0)
        return {
            "key": module,
            "title": title,
            "icon": icon,
            "tone": tone,
            "values": [
                {"label": "Aperturadas", "value": opened},
                {"label": second_label, "value": int(row.get(second_key) or 0)},
                {"label": "Total", "value": total},
            ],
            "progress": _percentage(opened, total),
        }

    total_closed = sum(int(row.get("cerradas") or 0) for row in rows)
    total_next = sum(int(row.get("proximas") or 0) for row in rows)
    total_calls = sum(int(row.get("total") or 0) for row in rows)
    result = [
        item("proyectos", "Proyectos RSU", "folder", "#1677E8", "En evaluación", "en_evaluacion"),
        item("actividades", "Actividades específicas", "activities", "#7758E8", "En evaluación", "en_evaluacion"),
        item("voluntariado", "Voluntariado", "volunteer", "#24A148", "Permanentes", "permanentes"),
        {
            "key": "cierres",
            "title": "Estado de cierre",
            "icon": "flag",
            "tone": "#F57C00",
            "values": [
                {"label": "Cerradas", "value": total_closed},
                {"label": "Próximas", "value": total_next},
                {"label": "Total", "value": total_calls},
            ],
            "progress": _percentage(total_closed, total_calls),
        },
    ]
    return result


def _type_distribution(user: AuthUser, period_id: str | None, faculty: str | None, call_type: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id, faculty, call_type)
    rows = fetch_all(
        f"""
        SELECT ({_type_case()}) AS modulo, COUNT(DISTINCT cv.convocatoria_id) AS cantidad
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        {_project_context_join()}
        WHERE {where}
        GROUP BY modulo
        """,
        params,
    )
    labels = {"proyectos": "Proyectos RSU", "actividades": "Actividades específicas", "voluntariado": "Voluntariado"}
    indexed = {str(row.get("modulo")): int(row.get("cantidad") or 0) for row in rows}
    order = ["proyectos", "actividades", "voluntariado"]
    return [{"etiqueta": labels[key], "cantidad": indexed.get(key, 0)} for key in order if call_type in (None, key)]


def _trend(user: AuthUser, period_id: str | None, faculty: str | None, call_type: str | None, periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    where, params = _where(user, period_id, faculty, call_type)
    rows = fetch_all(
        f"""
        SELECT DATE_FORMAT(cv.fecha_inicio,'%%Y-%%m') AS periodo,
               COUNT(DISTINCT cv.convocatoria_id) AS cantidad
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        {_project_context_join()}
        WHERE {where}
        GROUP BY DATE_FORMAT(cv.fecha_inicio,'%%Y-%%m')
        ORDER BY periodo
        """,
        params,
    )
    selected = next((row for row in periods if str(row.get("periodo_academico_id")) == str(period_id)), None)
    year = None
    if selected and selected.get("fecha_inicio"):
        start_value = selected.get("fecha_inicio")
        if hasattr(start_value, "year"):
            year = int(start_value.year)
        else:
            try:
                year = int(str(start_value)[:4])
            except (TypeError, ValueError):
                year = None
    if year is None:
        year = date.today().year
    indexed = {str(row.get("periodo")): int(row.get("cantidad") or 0) for row in rows}
    return [{"periodo": f"{year}-{month:02d}", "cantidad": indexed.get(f"{year}-{month:02d}", 0)} for month in range(1, 13)]


def _state_distribution(user: AuthUser, period_id: str | None, faculty: str | None, call_type: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id, faculty, call_type)
    state = "LOWER(COALESCE(ce.nombre,''))"
    rows = fetch_all(
        f"""
        SELECT CASE
                 WHEN {state} LIKE '%%observ%%' THEN 'Observadas'
                 WHEN {state}='cerrada' OR cv.fecha_limite_presentacion<CURDATE() THEN 'Cerradas'
                 WHEN cv.fecha_limite_presentacion BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 14 DAY) THEN 'Próximas a cerrar'
                 ELSE 'Vigentes'
               END AS etiqueta,
               COUNT(DISTINCT cv.convocatoria_id) AS cantidad
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        {_project_context_join()}
        WHERE {where}
        GROUP BY etiqueta
        """,
        params,
    )
    order = ["Vigentes", "Próximas a cerrar", "Cerradas", "Observadas"]
    indexed = {str(row.get("etiqueta")): int(row.get("cantidad") or 0) for row in rows}
    return [{"etiqueta": label, "cantidad": indexed.get(label, 0)} for label in order]


def _upcoming(user: AuthUser, period_id: str | None, faculty: str | None, call_type: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id, faculty, call_type)
    return fetch_all(
        f"""
        SELECT cv.convocatoria_id,
               CONCAT(tp.nombre,' - ',pa.nombre) AS convocatoria,
               COALESCE(MAX(NULLIF(TRIM(rf.responsable),'')),'Administración RSU') AS responsable,
               cv.fecha_limite_presentacion AS fecha
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        {_project_context_join()}
        WHERE cv.fecha_limite_presentacion>=CURDATE()
          AND LOWER(COALESCE(ce.nombre,''))<>'cerrada'
          AND {where}
        GROUP BY cv.convocatoria_id,tp.nombre,pa.nombre,cv.fecha_limite_presentacion
        ORDER BY cv.fecha_limite_presentacion ASC
        LIMIT 6
        """,
        params,
    )


def _faculty_participation(user: AuthUser, period_id: str | None, faculty: str | None, call_type: str | None) -> list[dict[str, Any]]:
    where, params = _where(user, period_id, faculty, call_type)
    rows = fetch_all(
        f"""
        SELECT COALESCE(NULLIF(TRIM(rf.facultad),''),'Sin facultad') AS facultad,
               COUNT(DISTINCT p.proyecto_id) AS convocatorias
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        {_project_context_join()}
        WHERE p.proyecto_id IS NOT NULL AND {where}
        GROUP BY facultad
        ORDER BY convocatorias DESC, facultad
        LIMIT 8
        """,
        params,
    )
    total = sum(int(row.get("convocatorias") or 0) for row in rows)
    return [
        {
            "facultad": str(row.get("facultad") or "Sin facultad"),
            "convocatorias": int(row.get("convocatorias") or 0),
            "participacion": round((int(row.get("convocatorias") or 0) / total * 100), 1) if total else 0.0,
        }
        for row in rows
    ]


def _scope_presentation(user: AuthUser) -> dict[str, Any]:
    if _is_general_view(user):
        return {
            "scope_mode": "institucional",
            "scope_label": "Vista institucional",
            "subtitle": "Seguimiento general de convocatorias, aperturas, cierres y postulaciones institucionales.",
            "metric_labels": {
                "registradas": "Convocatorias registradas",
                "vigentes": "Vigentes",
                "proximas": "Próximas a cerrar",
                "cerradas": "Cerradas",
                "postulaciones": "Postulaciones recibidas",
                "facultades": "Facultades participantes",
            },
        }

    role = _role(user)
    if role == "director_facultad":
        faculty = str(user.facultad_asignada or "su facultad").strip()
        return {
            "scope_mode": "facultad",
            "scope_label": f"Mi facultad · {faculty}",
            "subtitle": f"Convocatorias y postulaciones vinculadas a la {faculty}.",
            "metric_labels": {
                "registradas": "Convocatorias de la facultad",
                "vigentes": "Vigentes en la facultad",
                "proximas": "Próximas a cerrar",
                "cerradas": "Cerradas",
                "postulaciones": "Postulaciones de la facultad",
                "facultades": "Facultades relacionadas",
            },
        }
    if role == "vicepresidente_academico":
        return {
            "scope_mode": "despacho",
            "scope_label": "Mi despacho",
            "subtitle": "Convocatorias con expedientes y memorándums remitidos a la Vicepresidencia Académica.",
            "metric_labels": {
                "registradas": "Convocatorias remitidas",
                "vigentes": "Vigentes",
                "proximas": "Próximas a cerrar",
                "cerradas": "Cerradas",
                "postulaciones": "Expedientes remitidos",
                "facultades": "Facultades relacionadas",
            },
        }
    return {
        "scope_mode": "personal",
        "scope_label": "Mi participación",
        "subtitle": "Convocatorias vinculadas a sus proyectos y participaciones en responsabilidad social universitaria.",
        "metric_labels": {
            "registradas": "Convocatorias relacionadas",
            "vigentes": "Vigentes para mí",
            "proximas": "Próximas a cerrar",
            "cerradas": "Cerradas",
            "postulaciones": "Mis expedientes",
            "facultades": "Facultades relacionadas",
        },
    }


def _events(user: AuthUser) -> list[dict[str, Any]]:
    if not _is_general_view(user):
        rows = fetch_all(
            """
            SELECT n.titulo, n.mensaje, n.fecha_creacion, n.proyecto_id,
                   n.entidad_id, n.generada_por
            FROM rsu_notification n
            WHERE n.usuario_id=%s
              AND (LOWER(n.modulo) LIKE '%%convoc%%'
                   OR LOWER(n.tipo) LIKE '%%convoc%%')
            ORDER BY n.fecha_creacion DESC
            LIMIT 8
            """,
            (user.usuario_id,),
        )
        return [
            {
                "tipo": "convocatoria",
                "titulo": str(row.get("titulo") or "Actualización de convocatoria"),
                "mensaje": str(row.get("mensaje") or "Se registró una actualización relacionada con su perfil."),
                "fecha": row.get("fecha_creacion"),
                "referencia_id": row.get("proyecto_id") or row.get("entidad_id"),
                "responsable": str(row.get("generada_por") or "Sistema RSU"),
            }
            for row in rows
        ]

    rows = fetch_all(
        """
        SELECT accion, metodo_http, ruta, detalle, usuario_responsable, fecha_hora, recurso_id
        FROM rsu_audit_log
        WHERE estado_http BETWEEN 200 AND 399
          AND LOWER(ruta) LIKE '%%/convocatorias%%'
        ORDER BY fecha_hora DESC
        LIMIT 8
        """
    )
    events: list[dict[str, Any]] = []
    for row in rows:
        route = str(row.get("ruta") or "").lower()
        if "/activar" in route:
            title = "Convocatoria activada"
        elif "/cerrar" in route:
            title = "Convocatoria cerrada"
        elif str(row.get("metodo_http") or "").upper() in {"PUT", "PATCH"}:
            title = "Convocatoria actualizada"
        else:
            title = "Convocatoria creada"
        actor = str(row.get("usuario_responsable") or "Sistema")
        detail = str(row.get("detalle") or "").strip()
        message = detail or f"Acción registrada por {actor}."
        events.append(
            {
                "tipo": "convocatoria",
                "titulo": title,
                "mensaje": message,
                "fecha": row.get("fecha_hora"),
                "referencia_id": row.get("recurso_id"),
                "responsable": actor,
            }
        )
    return events

def build_convocations_dashboard(
    user: AuthUser,
    *,
    call_type: str | None = None,
    faculty: str | None = None,
    period_id: str | None = None,
) -> dict[str, Any]:
    selected_period, periods = _resolve_period(period_id)
    faculties = _faculties(user)
    selected_faculty = _resolve_faculty(user, faculty, faculties)
    selected_type = _resolve_type(call_type)
    presentation = _scope_presentation(user)
    return {
        "title": "Dashboard de Convocatorias",
        "subtitle": presentation["subtitle"],
        "scope_mode": presentation["scope_mode"],
        "scope_label": presentation["scope_label"],
        "metric_labels": presentation["metric_labels"],
        "types": _TYPE_OPTIONS,
        "faculties": faculties,
        "periods": periods,
        "selected_type": selected_type or "all",
        "selected_faculty": selected_faculty or "all",
        "selected_period_id": selected_period or "all",
        "faculty_locked": _role(user) == "director_facultad" and bool(str(user.facultad_asignada or "").strip()),
        "personalized": not _is_general_view(user),
        "can_manage": (
            _role(user) in {"administrador_rsu", "director_rsu"}
            if _role(user)
            else str(user.tipo_usuario or "").strip().lower() in {"administrativo", "administrador", "admin"}
        ),
        "counts": _summary(user, selected_period, selected_faculty, selected_type),
        "module_summaries": _module_summaries(user, selected_period, selected_faculty, selected_type),
        "type_distribution": _type_distribution(user, selected_period, selected_faculty, selected_type),
        "trend": _trend(user, selected_period, selected_faculty, selected_type, periods),
        "state_distribution": _state_distribution(user, selected_period, selected_faculty, selected_type),
        "upcoming_closures": _upcoming(user, selected_period, selected_faculty, selected_type),
        "faculty_participation": _faculty_participation(user, selected_period, selected_faculty, selected_type),
        "events": _events(user),
    }
