from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException

from app.db.mysql import fetch_all, fetch_one
from app.schemas.auth import AuthUser
from app.services.dashboard_period_filter import convocation_period_filter, project_period_filter
from app.services.rsu_hours import calculate_rsu_person_hours
from app.services.simple_xlsx import build_table_workbook


_REPORT_ROLES = {
    "administrador_rsu",
    "director_rsu",
    "director_facultad",
    "vicepresidente_academico",
}


def _role(user: AuthUser) -> str:
    return str(user.rol_sistema or "").strip().lower()


def _can_view(user: AuthUser) -> bool:
    role = _role(user)
    if role:
        return role in _REPORT_ROLES
    return str(user.tipo_usuario or "").strip().lower() in {"administrativo", "administrador", "admin"}


def _require_access(user: AuthUser) -> None:
    if not _can_view(user):
        raise HTTPException(status_code=403, detail="No tienes permisos para consultar los reportes institucionales.")


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


def _project_where(period_id: str | None, faculty: str | None) -> tuple[str, tuple[Any, ...]]:
    period_clause, period_values = project_period_filter(period_id, "p", "cv")
    clauses = [period_clause]
    params: list[Any] = list(period_values)
    if faculty:
        clauses.append("COALESCE(NULLIF(TRIM(rf.facultad),''),'Sin facultad')=%s")
        params.append(faculty)
    return " AND ".join(f"({clause})" for clause in clauses), tuple(params)


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


def _state_key() -> str:
    return "LOWER(REPLACE(REPLACE(COALESCE(ce.nombre,''),' ', '_'),'ó','o'))"


def _summary(period_id: str | None, faculty: str | None) -> dict[str, Any]:
    where, params = _project_where(period_id, faculty)
    module = _module_case()
    row = fetch_one(
        f"""
        SELECT COUNT(DISTINCT p.proyecto_id) AS reportes_generados,
               COUNT(DISTINCT CASE WHEN {module}='proyectos' THEN p.proyecto_id END) AS proyectos_reportados,
               COUNT(DISTINCT CASE WHEN {module}='actividades' THEN p.proyecto_id END) AS actividades_especificas,
               COUNT(DISTINCT CASE WHEN {module}='voluntariado' THEN p.proyecto_id END) AS voluntariados_reportados
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {_responsible_faculty_join()}
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
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {_responsible_faculty_join()}
        WHERE c.fecha_emision IS NOT NULL AND {where}
        """,
        params,
    ) or {}
    row["certificados_emitidos"] = int(certificates.get("total") or 0)
    row["horas_rsu_acumuladas"] = calculate_rsu_person_hours(
        where,
        params,
        extra_joins=_responsible_faculty_join(),
    )
    return row


def _module_summaries(period_id: str | None, faculty: str | None) -> list[dict[str, Any]]:
    where, params = _project_where(period_id, faculty)
    module = _module_case()
    state = _state_key()
    rows = fetch_all(
        f"""
        SELECT {module} AS modulo,
               COUNT(DISTINCT p.proyecto_id) AS total,
               COUNT(DISTINCT CASE WHEN {state} LIKE '%aprob%' OR {state} LIKE '%certific%'
                    OR {state} LIKE '%final%' OR {state} LIKE '%memorandum%' THEN p.proyecto_id END) AS aprobados,
               COUNT(DISTINCT CASE WHEN {state} LIKE '%ejecucion%' OR LOCATE('seguimiento',{state})>0
                    OR {state} LIKE '%cierre%' THEN p.proyecto_id END) AS seguimiento,
               COUNT(DISTINCT CASE WHEN {state} LIKE '%observ%' THEN p.proyecto_id END) AS observados
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {_responsible_faculty_join()}
        WHERE {where}
        GROUP BY modulo
        """,
        params,
    )
    by_module = {str(row.get("modulo")): row for row in rows}

    volunteer = fetch_one(
        f"""
        SELECT COUNT(DISTINCT CASE WHEN rvw.modalidad='interno' THEN p.proyecto_id END) AS internos,
               COUNT(DISTINCT CASE WHEN rvw.modalidad='externo' THEN p.proyecto_id END) AS externos
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {_responsible_faculty_join()}
        LEFT JOIN rsu_volunteer_workflow rvw ON rvw.proyecto_id=p.proyecto_id
        WHERE ({module})='voluntariado' AND {where}
        """,
        params,
    ) or {}

    call_period, call_period_values = convocation_period_filter(period_id, "cv")
    call_clauses = [call_period]
    call_params: list[Any] = list(call_period_values)
    calls = fetch_one(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN LOWER(ce.nombre)='abierta' AND cv.fecha_inicio<=CURDATE()
                         AND cv.fecha_limite_presentacion>=CURDATE() THEN 1 ELSE 0 END) AS vigentes,
               SUM(CASE WHEN cv.fecha_limite_presentacion<CURDATE() OR LOWER(ce.nombre)='cerrada'
                         THEN 1 ELSE 0 END) AS cerradas
        FROM convocatoria cv
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        WHERE {' AND '.join(call_clauses)}
        """,
        tuple(call_params),
    ) or {}

    project = by_module.get("proyectos", {})
    activity = by_module.get("actividades", {})
    volunteer_base = by_module.get("voluntariado", {})
    return [
        {
            "key": "proyectos",
            "title": "Proyectos RSU",
            "icon": "folder",
            "tone": "#1677E8",
            "values": [
                {"label": "Aprobados", "value": int(project.get("aprobados") or 0)},
                {"label": "En seguimiento", "value": int(project.get("seguimiento") or 0)},
                {"label": "Total", "value": int(project.get("total") or 0)},
            ],
            "progress": _percentage(project.get("aprobados"), project.get("total")),
        },
        {
            "key": "actividades",
            "title": "Actividades específicas",
            "icon": "activities",
            "tone": "#7758E8",
            "values": [
                {"label": "Ejecutadas", "value": int(activity.get("aprobados") or 0) + int(activity.get("seguimiento") or 0)},
                {"label": "Observadas", "value": int(activity.get("observados") or 0)},
                {"label": "Total", "value": int(activity.get("total") or 0)},
            ],
            "progress": _percentage((activity.get("aprobados") or 0) + (activity.get("seguimiento") or 0), activity.get("total")),
        },
        {
            "key": "voluntariado",
            "title": "Voluntariado",
            "icon": "volunteer",
            "tone": "#24A148",
            "values": [
                {"label": "Internos", "value": int(volunteer.get("internos") or 0)},
                {"label": "Externos", "value": int(volunteer.get("externos") or 0)},
                {"label": "Total", "value": int(volunteer_base.get("total") or 0)},
            ],
            "progress": _percentage((volunteer.get("internos") or 0) + (volunteer.get("externos") or 0), volunteer_base.get("total")),
        },
        {
            "key": "convocatorias",
            "title": "Convocatorias y cierres",
            "icon": "megaphone",
            "tone": "#F59E0B",
            "values": [
                {"label": "Vigentes", "value": int(calls.get("vigentes") or 0)},
                {"label": "Cerradas", "value": int(calls.get("cerradas") or 0)},
                {"label": "Total", "value": int(calls.get("total") or 0)},
            ],
            "progress": _percentage(calls.get("cerradas"), calls.get("total")),
        },
    ]


def _percentage(part: Any, total: Any) -> int:
    try:
        denominator = int(total or 0)
        numerator = int(part or 0)
    except (TypeError, ValueError):
        return 0
    return 0 if denominator <= 0 else max(0, min(100, round(numerator / denominator * 100)))


def _module_distribution(period_id: str | None, faculty: str | None, summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = {
        "proyectos": "Proyectos RSU",
        "actividades": "Actividades específicas",
        "voluntariado": "Voluntariado",
        "convocatorias": "Convocatorias y cierres",
    }
    return [
        {
            "key": row.get("key"),
            "etiqueta": labels.get(str(row.get("key")), str(row.get("title") or "")),
            "cantidad": int((row.get("values") or [{}, {}, {}])[-1].get("value") or 0),
        }
        for row in summaries
    ]


def _trend(period_id: str | None, faculty: str | None) -> list[dict[str, Any]]:
    where, params = _project_where(period_id, faculty)
    return fetch_all(
        f"""
        SELECT DATE_FORMAT(COALESCE(p.fecha_presentacion,p.fecha_creacion),'%Y-%m') AS periodo,
               COUNT(DISTINCT p.proyecto_id) AS cantidad
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {_responsible_faculty_join()}
        WHERE {where}
          AND COALESCE(p.fecha_presentacion,p.fecha_creacion)>=DATE_SUB(CURDATE(),INTERVAL 11 MONTH)
        GROUP BY periodo ORDER BY periodo
        """,
        params,
    )


def _state_distribution(period_id: str | None, faculty: str | None) -> list[dict[str, Any]]:
    where, params = _project_where(period_id, faculty)
    state = _state_key()
    row = fetch_one(
        f"""
        SELECT COUNT(DISTINCT CASE WHEN {state} LIKE '%aprob%' OR {state} LIKE '%certific%'
                   OR {state} LIKE '%final%' OR {state} LIKE '%memorandum%' THEN p.proyecto_id END) AS aprobados,
               COUNT(DISTINCT CASE WHEN {state} LIKE '%evaluacion%' OR {state} LIKE '%presentad%'
                   OR {state} LIKE '%cierre%' THEN p.proyecto_id END) AS evaluacion,
               COUNT(DISTINCT CASE WHEN {state} LIKE '%observ%' THEN p.proyecto_id END) AS observados,
               COUNT(DISTINCT CASE WHEN LOCATE('subsan',{state})>0 THEN p.proyecto_id END) AS subsanados
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {_responsible_faculty_join()}
        WHERE {where}
        """,
        params,
    ) or {}
    return [
        {"etiqueta": "Aprobados", "cantidad": int(row.get("aprobados") or 0), "color": "#24A148"},
        {"etiqueta": "En evaluación", "cantidad": int(row.get("evaluacion") or 0), "color": "#F59E0B"},
        {"etiqueta": "Observados", "cantidad": int(row.get("observados") or 0), "color": "#EF5350"},
        {"etiqueta": "Subsanados", "cantidad": int(row.get("subsanados") or 0), "color": "#7758E8"},
    ]


def _faculty_participation(period_id: str | None, faculty: str | None) -> list[dict[str, Any]]:
    where, params = _project_where(period_id, faculty)
    rows = fetch_all(
        f"""
        SELECT COALESCE(NULLIF(TRIM(rf.facultad),''),'Sin facultad') AS facultad,
               COUNT(DISTINCT p.proyecto_id) AS reportes
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {_responsible_faculty_join()}
        WHERE {where}
        GROUP BY COALESCE(NULLIF(TRIM(rf.facultad),''),'Sin facultad')
        ORDER BY reportes DESC, facultad LIMIT 8
        """,
        params,
    )
    total = sum(int(row.get("reportes") or 0) for row in rows)
    for index, row in enumerate(rows, start=1):
        value = int(row.get("reportes") or 0)
        row["rank"] = index
        row["participacion"] = 0 if total <= 0 else round(value / total * 100, 1)
    return rows


def _deadlines(period_id: str | None, faculty: str | None) -> list[dict[str, Any]]:
    where, params = _project_where(period_id, faculty)
    return fetch_all(
        f"""
        SELECT p.titulo AS proceso,
               COALESCE(resp.responsable,'Sin responsable') AS responsable,
               COALESCE(rpw.fecha_limite_actual,saw.fecha_limite_subsanacion,
                        rvw.fecha_limite_subsanacion,p.fecha_fin) AS fecha
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {_responsible_faculty_join()}
        LEFT JOIN rsu_project_workflow rpw ON rpw.proyecto_id=p.proyecto_id
        LEFT JOIN rsu_specific_activity_workflow saw ON saw.proyecto_id=p.proyecto_id
        LEFT JOIN rsu_volunteer_workflow rvw ON rvw.proyecto_id=p.proyecto_id
        LEFT JOIN (
          SELECT rp.proyecto_id, MAX(CONCAT(pe.nombres,' ',pe.apellidos)) AS responsable
          FROM rol_proyecto rp
          INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id AND LOWER(cr.nombre)='responsable'
          INNER JOIN persona pe ON pe.persona_id=rp.persona_id
          WHERE rp.activo=1 GROUP BY rp.proyecto_id
        ) resp ON resp.proyecto_id=p.proyecto_id
        WHERE {where}
          AND COALESCE(rpw.fecha_limite_actual,saw.fecha_limite_subsanacion,
                       rvw.fecha_limite_subsanacion,p.fecha_fin)>=CURDATE()
        ORDER BY fecha LIMIT 7
        """,
        params,
    )


def _recent_activity(period_id: str | None, faculty: str | None) -> list[dict[str, Any]]:
    where, params = _project_where(period_id, faculty)
    rows = fetch_all(
        f"""
        SELECT wh.fecha_cambio AS fecha,
               CASE
                 WHEN LOWER(COALESCE(wh.estado_nuevo,'')) LIKE '%observ%' THEN 'Reporte observado'
                 WHEN LOWER(COALESCE(wh.estado_nuevo,'')) LIKE '%aprob%' THEN 'Reporte aprobado'
                 WHEN LOWER(COALESCE(wh.estado_nuevo,'')) LIKE '%certific%' THEN 'Certificación registrada'
                 ELSE 'Actualización de reporte'
               END AS titulo,
               CONCAT(p.titulo, CASE WHEN NULLIF(TRIM(COALESCE(wh.comentario,'')),'') IS NULL
                    THEN '' ELSE CONCAT(' · ',wh.comentario) END) AS mensaje,
               COALESCE(CONCAT(pe.nombres,' ',pe.apellidos),'Sistema RSU') AS actor
        FROM rsu_workflow_history wh
        INNER JOIN proyecto p ON p.proyecto_id=wh.proyecto_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {_responsible_faculty_join()}
        LEFT JOIN persona pe ON pe.persona_id=wh.cambiado_por
        WHERE {where}
        ORDER BY wh.fecha_cambio DESC LIMIT 8
        """,
        params,
    )
    return [
        {
            "tipo": "reporte",
            "titulo": row.get("titulo"),
            "mensaje": row.get("mensaje"),
            "fecha": row.get("fecha"),
            "actor": row.get("actor"),
        }
        for row in rows
    ]


def build_reports_dashboard(
    user: AuthUser,
    faculty: str | None = None,
    period_id: str | None = None,
) -> dict[str, Any]:
    _require_access(user)
    faculties = _faculties()
    selected_period, periods = _resolve_period(period_id)
    selected_faculty = _resolve_faculty(user, faculty, faculties)
    if _role(user) == "director_facultad" and selected_faculty and selected_faculty not in faculties:
        faculties = [selected_faculty]
    summaries = _module_summaries(selected_period, selected_faculty)
    counts = _summary(selected_period, selected_faculty)
    selected_period_row = next(
        (row for row in periods if str(row.get("periodo_academico_id")) == str(selected_period)),
        None,
    )
    return {
        "module": "reportes",
        "title": "Dashboard de Reportes",
        "subtitle": "Resumen analítico y exportable de la información institucional.",
        "counts": {
            "reportes_generados": int(counts.get("reportes_generados") or 0),
            "proyectos_reportados": int(counts.get("proyectos_reportados") or 0),
            "actividades_especificas": int(counts.get("actividades_especificas") or 0),
            "voluntariados_reportados": int(counts.get("voluntariados_reportados") or 0),
            "horas_rsu_acumuladas": float(counts.get("horas_rsu_acumuladas") or 0),
            "certificados_emitidos": int(counts.get("certificados_emitidos") or 0),
        },
        "faculties": faculties,
        "periods": periods,
        "selected_faculty": selected_faculty or "all",
        "selected_period_id": selected_period or "all",
        "selected_period_name": str((selected_period_row or {}).get("nombre") or "Todos los ciclos"),
        "module_summaries": summaries,
        "module_distribution": _module_distribution(selected_period, selected_faculty, summaries),
        "trend": _trend(selected_period, selected_faculty),
        "state_distribution": _state_distribution(selected_period, selected_faculty),
        "faculty_participation": _faculty_participation(selected_period, selected_faculty),
        "deadlines": _deadlines(selected_period, selected_faculty),
        "meta": {"generated_at": datetime.now().isoformat()},
    }


def _normalize_key(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    return "".join(char for char in text if unicodedata.category(char) != "Mn").lower().replace(" ", "_")


def _walk_numeric_beneficiaries(value: Any) -> list[int]:
    found: list[int] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalize_key(key)
            if "benefici" in normalized and isinstance(item, (int, float, str)):
                match = re.search(r"\b(\d{1,7})\b", str(item))
                if match:
                    found.append(int(match.group(1)))
            found.extend(_walk_numeric_beneficiaries(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_numeric_beneficiaries(item))
    return found


def _beneficiary_counts(project_ids: list[str], population_by_project: dict[str, Any]) -> dict[str, int]:
    if not project_ids:
        return {}
    placeholders = ",".join(["%s"] * len(project_ids))
    rows = fetch_all(
        f"""
        SELECT proyecto_id, contenido_json
        FROM rsu_annex_submission
        WHERE es_actual=1 AND proyecto_id IN ({placeholders})
        """,
        tuple(project_ids),
    )
    values: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        try:
            payload = json.loads(str(row.get("contenido_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        values[str(row.get("proyecto_id"))].extend(_walk_numeric_beneficiaries(payload))
    result: dict[str, int] = {}
    for project_id in project_ids:
        candidates = values.get(project_id, [])
        if candidates:
            result[project_id] = max(candidates)
            continue
        text = str(population_by_project.get(project_id) or "")
        match = re.search(r"\b(\d{1,7})\b", text)
        result[project_id] = int(match.group(1)) if match else 0
    return result


def _export_rows(period_id: str | None, faculty: str | None) -> list[dict[str, Any]]:
    where, params = _project_where(period_id, faculty)
    rows = fetch_all(
        f"""
        SELECT p.proyecto_id, p.titulo, p.poblacion_objetivo,
               CONCAT(la.codigo,'-',la.nombre) AS linea_accion,
               tp.nombre AS tipo, ce.nombre AS estado,
               COALESCE(NULLIF(TRIM(rf.facultad),''),'Sin facultad') AS facultad,
               COALESCE(
                 saw.fecha_aprobacion_final,rvw.fecha_aprobacion_final,rpw.fecha_finalizacion,
                 saw.fecha_aprobacion_inicial,rvw.fecha_aprobacion_inicial,rpw.fecha_aprobacion_direccion,
                 (SELECT MAX(ep.fecha_evaluacion) FROM evaluacion_proyecto ep
                   WHERE ep.proyecto_id=p.proyecto_id AND ep.resultado='aprobado')
               ) AS fecha_aprobacion,
               GROUP_CONCAT(DISTINCT CONCAT(pe.nombres,' ',pe.apellidos,'-',cr.nombre)
                 ORDER BY CASE WHEN LOWER(cr.nombre)='responsable' THEN 0 ELSE 1 END,
                          pe.apellidos,pe.nombres SEPARATOR '\n') AS participantes,
               COUNT(DISTINCT CASE WHEN rp.activo=1 AND LOWER(cr.nombre)<>'responsable'
                    THEN rp.persona_id END) AS colaboradores,
               COALESCE(
                 (SELECT ae.observaciones FROM rsu_annex_evaluation ae
                   WHERE ae.proyecto_id=p.proyecto_id AND NULLIF(TRIM(ae.observaciones),'') IS NOT NULL
                   ORDER BY ae.fecha_evaluacion DESC LIMIT 1),
                 (SELECT ep2.observaciones FROM evaluacion_proyecto ep2
                   WHERE ep2.proyecto_id=p.proyecto_id AND NULLIF(TRIM(ep2.observaciones),'') IS NOT NULL
                   ORDER BY ep2.fecha_evaluacion DESC LIMIT 1),
                 ''
               ) AS observaciones
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        {_responsible_faculty_join()}
        LEFT JOIN rsu_project_workflow rpw ON rpw.proyecto_id=p.proyecto_id
        LEFT JOIN rsu_specific_activity_workflow saw ON saw.proyecto_id=p.proyecto_id
        LEFT JOIN rsu_volunteer_workflow rvw ON rvw.proyecto_id=p.proyecto_id
        LEFT JOIN rol_proyecto rp ON rp.proyecto_id=p.proyecto_id AND rp.activo=1
        LEFT JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        LEFT JOIN persona pe ON pe.persona_id=rp.persona_id
        WHERE {where}
        GROUP BY p.proyecto_id,p.titulo,p.poblacion_objetivo,la.codigo,la.nombre,tp.nombre,
                 ce.nombre,rf.facultad,saw.fecha_aprobacion_final,rvw.fecha_aprobacion_final,
                 rpw.fecha_finalizacion,saw.fecha_aprobacion_inicial,rvw.fecha_aprobacion_inicial,
                 rpw.fecha_aprobacion_direccion
        ORDER BY COALESCE(fecha_aprobacion,p.fecha_presentacion,p.fecha_creacion) DESC,p.titulo
        """,
        params,
    )
    ids = [str(row.get("proyecto_id")) for row in rows]
    beneficiaries = _beneficiary_counts(ids, {str(row.get("proyecto_id")): row.get("poblacion_objetivo") for row in rows})
    for row in rows:
        project_id = str(row.get("proyecto_id"))
        approved_at = row.get("fecha_aprobacion")
        if isinstance(approved_at, (date, datetime)):
            date_text = approved_at.strftime("%d/%m/%Y")
        else:
            date_text = str(approved_at or "")
        state = str(row.get("estado") or "Sin estado")
        row["aprobacion"] = f"Aprobado · {date_text}" if date_text else state
        row["beneficiarios"] = int(beneficiaries.get(project_id) or 0)
    return rows


def build_reports_excel(
    user: AuthUser,
    faculty: str | None = None,
    period_id: str | None = None,
) -> tuple[bytes, str]:
    _require_access(user)
    faculties = _faculties()
    selected_period, periods = _resolve_period(period_id)
    selected_faculty = _resolve_faculty(user, faculty, faculties)
    period = next((row for row in periods if str(row.get("periodo_academico_id")) == str(selected_period)), None)
    period_name = str((period or {}).get("nombre") or "Todos los ciclos")
    faculty_name = selected_faculty or "Todas las facultades"
    rows = _export_rows(selected_period, selected_faculty)
    table = []
    for index, row in enumerate(rows, start=1):
        table.append(
            [
                index,
                row.get("aprobacion") or "",
                row.get("titulo") or "",
                row.get("participantes") or "Sin participantes registrados",
                row.get("linea_accion") or "",
                row.get("tipo") or "",
                int(row.get("colaboradores") or 0),
                int(row.get("beneficiarios") or 0),
                row.get("observaciones") or "",
            ]
        )
    headers = [
        "N°",
        "Aprobación",
        "Nombre del proyecto",
        "Participantes (nombre-cargo)",
        "Línea de acción",
        "Tipo",
        "N° de colaboradores",
        "N° de beneficiarios",
        "Obs.",
    ]
    subtitle = f"Facultad: {faculty_name}  |  Ciclo académico: {period_name}  |  Registros: {len(table)}"
    content = build_table_workbook(
        title="REPORTE INSTITUCIONAL DE RESPONSABILIDAD SOCIAL UNIVERSITARIA",
        subtitle=subtitle,
        headers=headers,
        rows=table,
        numeric_columns={0, 6, 7},
        sheet_name="Reporte RSU",
    )
    faculty_token = re.sub(r"[^A-Za-z0-9_-]+", "_", faculty_name).strip("_") or "todas"
    period_token = re.sub(r"[^A-Za-z0-9_-]+", "_", period_name).strip("_") or "todos"
    return content, f"reporte_rsu_{faculty_token}_{period_token}.xlsx"
