from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException

from app.db.mysql import fetch_all, fetch_one
from app.schemas.auth import AuthUser
from app.services.dashboard_period_filter import project_period_filter


_EVALUATION_ROLES = {"administrador_rsu", "director_rsu", "director_facultad"}


def _role(user: AuthUser) -> str:
    return str(user.rol_sistema or "").strip().lower()


def _require_access(user: AuthUser) -> None:
    role = _role(user)
    if role and role not in _EVALUATION_ROLES:
        raise HTTPException(status_code=403, detail="No tienes permisos para consultar el dashboard de evaluaciones.")
    if not role and str(user.tipo_usuario or "").strip().lower() not in {"administrativo", "administrador", "admin"}:
        raise HTTPException(status_code=403, detail="No tienes permisos para consultar el dashboard de evaluaciones.")


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
    if not active and periods:
        active = periods[0]
    return (str(active.get("periodo_academico_id")) if active else None), periods


def _faculties() -> list[str]:
    rows = fetch_all(
        """
        SELECT DISTINCT TRIM(facultad) AS facultad
        FROM departamento_academico
        WHERE activo=1 AND TRIM(COALESCE(facultad,''))<>''
        ORDER BY facultad
        """
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


def _resolve_module(value: str | None) -> str | None:
    raw = str(value or "").strip().lower()
    aliases = {
        "proyectos": "proyectos",
        "proyecto": "proyectos",
        "proyectos_rsu": "proyectos",
        "actividades": "actividades",
        "actividad": "actividades",
        "actividades_especificas": "actividades",
        "voluntariado": "voluntariado",
        "voluntariados": "voluntariado",
    }
    return aliases.get(raw)


def _responsible_faculty_join() -> str:
    return """
        LEFT JOIN (
            SELECT rp_fac.proyecto_id,
                   MAX(COALESCE(dd.facultad,de.facultad,usr.facultad,'')) AS facultad
            FROM rol_proyecto rp_fac
            INNER JOIN catalogo_rol cr_fac ON cr_fac.rol_id=rp_fac.rol_id
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


def _module_case() -> str:
    return """
        CASE
          WHEN LOWER(tp.nombre) LIKE '%volunt%' THEN 'voluntariado'
          WHEN LOWER(tp.nombre) LIKE '%actividades espec%'
            OR LOWER(tp.nombre) LIKE '%proyeccion social%'
            OR LOWER(tp.nombre) LIKE '%proyección social%' THEN 'actividades'
          ELSE 'proyectos'
        END
    """


def _status_case() -> str:
    state = "LOWER(REPLACE(REPLACE(COALESCE(a.estado,e.resultado,'pendiente'),' ', '_'),'ó','o'))"
    return f"""
        CASE
          WHEN {state} LIKE '%aprob%' OR {state} LIKE '%convalid%' THEN 'aprobada'
          WHEN ({state} LIKE '%observ%' OR {state} LIKE '%no_convalid%' OR {state} LIKE '%con_observ%')
               AND e.fecha_limite_subsanacion IS NOT NULL
               AND e.fecha_limite_subsanacion>=CURDATE() THEN 'subsanacion'
          WHEN {state} LIKE '%observ%' OR {state} LIKE '%no_convalid%' OR {state} LIKE '%con_observ%' THEN 'observada'
          ELSE 'pendiente'
        END
    """


def _where(period_id: str | None, faculty: str | None, module: str | None) -> tuple[str, tuple[Any, ...]]:
    period_clause, period_values = project_period_filter(period_id, "p", "cv")
    clauses = [period_clause]
    params: list[Any] = list(period_values)
    if faculty:
        clauses.append("COALESCE(NULLIF(TRIM(rf.facultad),''),'Sin facultad')=%s")
        params.append(faculty)
    if module:
        clauses.append(f"({_module_case()})=%s")
        params.append(module)
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


def _counts(period_id: str | None, faculty: str | None, module: str | None) -> dict[str, int]:
    where, params = _where(period_id, faculty, module)
    status = _status_case()
    row = fetch_one(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN ({status})='pendiente' THEN 1 ELSE 0 END) AS pendientes,
               SUM(CASE WHEN ({status})='aprobada' THEN 1 ELSE 0 END) AS aprobadas,
               SUM(CASE WHEN ({status})='observada' THEN 1 ELSE 0 END) AS observadas,
               SUM(CASE WHEN ({status})='subsanacion' THEN 1 ELSE 0 END) AS subsanacion,
               COUNT(DISTINCT CASE WHEN a.activo=1 AND a.revisor_persona_id IS NOT NULL
                                   THEN a.revisor_persona_id END) AS revisores
        FROM rsu_project_review_assignment a
        INNER JOIN proyecto p ON p.proyecto_id=a.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        {_responsible_faculty_join()}
        LEFT JOIN rsu_annex_evaluation e ON e.evaluation_id=a.evaluation_id
        WHERE {where}
        """,
        params,
    ) or {}
    return {key: int(row.get(key) or 0) for key in ("total", "pendientes", "aprobadas", "observadas", "subsanacion", "revisores")}


def _module_summaries(period_id: str | None, faculty: str | None, module: str | None) -> list[dict[str, Any]]:
    where, params = _where(period_id, faculty, module)
    status = _status_case()
    rows = fetch_all(
        f"""
        SELECT ({_module_case()}) AS modulo,
               COUNT(*) AS total,
               SUM(CASE WHEN a.anexo_numero IN (4,9,12) THEN 1 ELSE 0 END) AS iniciales,
               SUM(CASE WHEN a.anexo_numero IN (7,10,13) THEN 1 ELSE 0 END) AS finales,
               SUM(CASE WHEN ({status})='aprobada' THEN 1 ELSE 0 END) AS aprobadas,
               SUM(CASE WHEN ({status}) IN ('observada','subsanacion') THEN 1 ELSE 0 END) AS observadas
        FROM rsu_project_review_assignment a
        INNER JOIN proyecto p ON p.proyecto_id=a.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        {_responsible_faculty_join()}
        LEFT JOIN rsu_annex_evaluation e ON e.evaluation_id=a.evaluation_id
        WHERE {where}
        GROUP BY modulo
        """,
        params,
    )
    by_module = {str(row.get("modulo")): row for row in rows}
    workload = fetch_one(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN LOWER(a.rol_revisor) IN ('administrador_rsu','director_rsu') THEN 1 ELSE 0 END) AS administradores,
               SUM(CASE WHEN LOWER(a.rol_revisor)='director_facultad' THEN 1 ELSE 0 END) AS directores,
               SUM(CASE WHEN ({status})='aprobada' THEN 1 ELSE 0 END) AS completadas
        FROM rsu_project_review_assignment a
        INNER JOIN proyecto p ON p.proyecto_id=a.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        {_responsible_faculty_join()}
        LEFT JOIN rsu_annex_evaluation e ON e.evaluation_id=a.evaluation_id
        WHERE {where}
        """,
        params,
    ) or {}
    project = by_module.get("proyectos", {})
    activity = by_module.get("actividades", {})
    volunteer = by_module.get("voluntariado", {})
    return [
        {
            "key": "proyectos",
            "title": "Proyectos RSU",
            "icon": "folder",
            "tone": "#1677E8",
            "values": [
                {"label": "Iniciales", "value": int(project.get("iniciales") or 0)},
                {"label": "Finales", "value": int(project.get("finales") or 0)},
                {"label": "Total", "value": int(project.get("total") or 0)},
            ],
            "progress": _percentage(project.get("aprobadas"), project.get("total")),
        },
        {
            "key": "actividades",
            "title": "Actividades específicas",
            "icon": "activities",
            "tone": "#7758E8",
            "values": [
                {"label": "Aprobadas", "value": int(activity.get("aprobadas") or 0)},
                {"label": "Observadas", "value": int(activity.get("observadas") or 0)},
                {"label": "Total", "value": int(activity.get("total") or 0)},
            ],
            "progress": _percentage(activity.get("aprobadas"), activity.get("total")),
        },
        {
            "key": "voluntariado",
            "title": "Voluntariado",
            "icon": "volunteer",
            "tone": "#24A148",
            "values": [
                {"label": "Iniciales", "value": int(volunteer.get("iniciales") or 0)},
                {"label": "Finales", "value": int(volunteer.get("finales") or 0)},
                {"label": "Total", "value": int(volunteer.get("total") or 0)},
            ],
            "progress": _percentage(volunteer.get("aprobadas"), volunteer.get("total")),
        },
        {
            "key": "carga",
            "title": "Carga de revisión",
            "icon": "users",
            "tone": "#F57C00",
            "values": [
                {"label": "Admin. RSU", "value": int(workload.get("administradores") or 0)},
                {"label": "Directores", "value": int(workload.get("directores") or 0)},
                {"label": "Total", "value": int(workload.get("total") or 0)},
            ],
            "progress": _percentage(workload.get("completadas"), workload.get("total")),
        },
    ]


def _module_distribution(period_id: str | None, faculty: str | None, module: str | None) -> list[dict[str, Any]]:
    where, params = _where(period_id, faculty, module)
    rows = fetch_all(
        f"""
        SELECT ({_module_case()}) AS modulo, COUNT(*) AS cantidad
        FROM rsu_project_review_assignment a
        INNER JOIN proyecto p ON p.proyecto_id=a.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        {_responsible_faculty_join()}
        WHERE {where}
        GROUP BY modulo
        """,
        params,
    )
    labels = {"proyectos": "Proyectos RSU", "actividades": "Actividades específicas", "voluntariado": "Voluntariado"}
    order = ["proyectos", "actividades", "voluntariado"]
    indexed = {str(row.get("modulo")): int(row.get("cantidad") or 0) for row in rows}
    return [{"etiqueta": labels[key], "cantidad": indexed.get(key, 0)} for key in order if module in (None, key)]


def _trend(period_id: str | None, faculty: str | None, module: str | None) -> list[dict[str, Any]]:
    where, params = _where(period_id, faculty, module)
    rows = fetch_all(
        f"""
        SELECT DATE_FORMAT(COALESCE(a.fecha_revision,a.fecha_asignacion),'%Y-%m') AS periodo,
               COUNT(*) AS cantidad
        FROM rsu_project_review_assignment a
        INNER JOIN proyecto p ON p.proyecto_id=a.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        {_responsible_faculty_join()}
        WHERE {where}
        GROUP BY DATE_FORMAT(COALESCE(a.fecha_revision,a.fecha_asignacion),'%Y-%m')
        ORDER BY periodo
        """,
        params,
    )
    return rows[-12:]


def _state_distribution(period_id: str | None, faculty: str | None, module: str | None) -> list[dict[str, Any]]:
    counts = _counts(period_id, faculty, module)
    return [
        {"etiqueta": "Aprobadas", "cantidad": counts["aprobadas"], "color": "#24A148"},
        {"etiqueta": "Pendientes", "cantidad": counts["pendientes"], "color": "#F59E0B"},
        {"etiqueta": "Observadas", "cantidad": counts["observadas"], "color": "#EF5350"},
        {"etiqueta": "En subsanación", "cantidad": counts["subsanacion"], "color": "#7758E8"},
    ]


def _deadlines(period_id: str | None, faculty: str | None, module: str | None) -> list[dict[str, Any]]:
    where, params = _where(period_id, faculty, module)
    status = _status_case()
    return fetch_all(
        f"""
        SELECT CONCAT(
                 CASE ({_module_case()})
                   WHEN 'voluntariado' THEN 'Revisión de voluntariado'
                   WHEN 'actividades' THEN 'Evaluación de actividad específica'
                   ELSE 'Evaluación de proyecto RSU'
                 END,
                 ' · Anexo ',a.anexo_numero
               ) AS proceso,
               COALESCE(CONCAT(rev.nombres,' ',rev.apellidos),'Por asignar') AS responsable,
               CASE WHEN ({status})='subsanacion' THEN e.fecha_limite_subsanacion
                    ELSE DATE_ADD(DATE(a.fecha_asignacion), INTERVAL 3 DAY) END AS fecha
        FROM rsu_project_review_assignment a
        INNER JOIN proyecto p ON p.proyecto_id=a.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        {_responsible_faculty_join()}
        LEFT JOIN rsu_annex_evaluation e ON e.evaluation_id=a.evaluation_id
        LEFT JOIN persona rev ON rev.persona_id=a.revisor_persona_id
        WHERE {where} AND ({status}) IN ('pendiente','subsanacion')
        ORDER BY fecha ASC, a.fecha_asignacion ASC
        LIMIT 7
        """,
        params,
    )


def _reviewer_load(period_id: str | None, faculty: str | None, module: str | None) -> list[dict[str, Any]]:
    where, params = _where(period_id, faculty, module)
    rows = fetch_all(
        f"""
        SELECT COALESCE(CONCAT(rev.nombres,' ',rev.apellidos),'Sin asignar') AS evaluador,
               COUNT(*) AS evaluaciones
        FROM rsu_project_review_assignment a
        INNER JOIN proyecto p ON p.proyecto_id=a.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        {_responsible_faculty_join()}
        LEFT JOIN persona rev ON rev.persona_id=a.revisor_persona_id
        WHERE {where}
        GROUP BY COALESCE(CONCAT(rev.nombres,' ',rev.apellidos),'Sin asignar')
        ORDER BY evaluaciones DESC, evaluador
        LIMIT 8
        """,
        params,
    )
    total = sum(int(row.get("evaluaciones") or 0) for row in rows)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
        row["participacion"] = 0 if total <= 0 else round(int(row.get("evaluaciones") or 0) / total * 100, 1)
    return rows


def _recent_activity(period_id: str | None, faculty: str | None, module: str | None) -> list[dict[str, Any]]:
    where, params = _where(period_id, faculty, module)
    rows = fetch_all(
        f"""
        SELECT e.fecha_evaluacion AS fecha,
               e.resultado,
               e.anexo_numero,
               p.titulo AS proyecto,
               COALESCE(CONCAT(pe.nombres,' ',pe.apellidos),'Sistema RSU') AS actor,
               NULLIF(TRIM(COALESCE(e.observaciones,'')),'') AS observaciones
        FROM rsu_annex_evaluation e
        INNER JOIN proyecto p ON p.proyecto_id=e.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        {_responsible_faculty_join()}
        LEFT JOIN persona pe ON pe.persona_id=e.evaluador_persona_id
        LEFT JOIN rsu_project_review_assignment a ON a.evaluation_id=e.evaluation_id
        WHERE {where}
        ORDER BY e.fecha_evaluacion DESC
        LIMIT 10
        """,
        params,
    )
    events: list[dict[str, Any]] = []
    for row in rows:
        result = str(row.get("resultado") or "").lower()
        observed = "observ" in result or "no_convalid" in result
        title = "Observación registrada" if observed else "Evaluación aprobada"
        message = f"Anexo {row.get('anexo_numero')}: {row.get('proyecto') or 'Expediente RSU'}"
        if row.get("observaciones"):
            message += f" · {row.get('observaciones')}"
        events.append(
            {
                "tipo": "observacion" if observed else "aprobacion",
                "titulo": title,
                "mensaje": message,
                "fecha": row.get("fecha"),
                "actor": row.get("actor"),
            }
        )
    return events


def build_evaluations_dashboard(
    user: AuthUser,
    module: str | None = None,
    faculty: str | None = None,
    period_id: str | None = None,
) -> dict[str, Any]:
    _require_access(user)
    faculties = _faculties()
    selected_period, periods = _resolve_period(period_id)
    selected_faculty = _resolve_faculty(user, faculty, faculties)
    selected_module = _resolve_module(module)
    if _role(user) == "director_facultad" and selected_faculty and selected_faculty not in faculties:
        faculties = [selected_faculty]
    counts = _counts(selected_period, selected_faculty, selected_module)
    selected_period_row = next(
        (row for row in periods if str(row.get("periodo_academico_id")) == str(selected_period)),
        None,
    )
    return {
        "module": "evaluaciones",
        "title": "Dashboard de Evaluaciones",
        "subtitle": "Seguimiento integral de revisiones, observaciones y aprobaciones institucionales.",
        "counts": {
            "evaluaciones_registradas": counts["total"],
            "pendientes": counts["pendientes"],
            "aprobadas": counts["aprobadas"],
            "observadas": counts["observadas"],
            "en_subsanacion": counts["subsanacion"],
            "revisores_activos": counts["revisores"],
        },
        "modules": [
            {"key": "all", "name": "Todos los módulos"},
            {"key": "proyectos", "name": "Proyectos RSU"},
            {"key": "actividades", "name": "Actividades específicas"},
            {"key": "voluntariado", "name": "Voluntariado"},
        ],
        "faculties": faculties,
        "periods": periods,
        "selected_module": selected_module or "all",
        "selected_faculty": selected_faculty or "all",
        "selected_period_id": selected_period or "all",
        "selected_period_name": str((selected_period_row or {}).get("nombre") or "Todos los ciclos"),
        "module_summaries": _module_summaries(selected_period, selected_faculty, selected_module),
        "module_distribution": _module_distribution(selected_period, selected_faculty, selected_module),
        "trend": _trend(selected_period, selected_faculty, selected_module),
        "state_distribution": _state_distribution(selected_period, selected_faculty, selected_module),
        "deadlines": _deadlines(selected_period, selected_faculty, selected_module),
        "reviewer_load": _reviewer_load(selected_period, selected_faculty, selected_module),
        "events": _recent_activity(selected_period, selected_faculty, selected_module),
        "meta": {"generated_at": datetime.now().isoformat()},
    }
