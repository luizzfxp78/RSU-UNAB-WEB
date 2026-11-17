from __future__ import annotations

import json
from typing import Any

from app.db.mysql import fetch_all


def _number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(number, 0.0)


def _json_hours(value: Any) -> float:
    if isinstance(value, dict):
        data = value
    else:
        try:
            data = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
    return _number(data.get("duracion_horas"))


def calculate_rsu_person_hours(
    where_sql: str,
    params: tuple[Any, ...] | list[Any],
    *,
    extra_joins: str = "",
) -> float:
    """Calcula horas-persona reales de los participantes de todas las acciones.

    Prioridad por expediente:
    1. Horas certificadas, porque ya están acreditadas por participante.
    2. Voluntariado: suma de jornadas individuales registradas.
    3. Actividad específica: horas del informe final por cada participante activo.
    4. Proyecto RSU: duración del Anexo 6 (o Anexo 5 en avance) por participante.
    5. Compatibilidad histórica: suma de ``rol_proyecto.horas_acumuladas``.

    De esta manera se contabilizan responsable, corresponsables y colaboradores
    activos sin limitar el cálculo a un solo tipo de proyecto.
    """
    rows = fetch_all(
        f"""
        SELECT p.proyecto_id,
               CASE
                 WHEN LOWER(tp.nombre) LIKE '%%volunt%%' THEN 'voluntariado'
                 WHEN LOWER(tp.nombre) LIKE '%%actividades espec%%'
                   OR LOWER(tp.nombre) LIKE '%%proyeccion social%%'
                   OR LOWER(tp.nombre) LIKE '%%proyección social%%' THEN 'actividades'
                 ELSE 'proyectos'
               END AS modulo,
               LOWER(COALESCE(ce.nombre,'')) AS estado,
               COALESCE(participantes.cantidad,0) AS participantes,
               COALESCE(certificados.horas,0) AS horas_certificadas,
               COALESCE(jornadas.horas,0) AS horas_jornadas,
               COALESCE(legado.horas,0) AS horas_legado,
               COALESCE(saw.horas_totales,0) AS horas_actividad,
               (
                   SELECT s6.contenido_json
                   FROM rsu_annex_submission s6
                   WHERE s6.proyecto_id=p.proyecto_id AND s6.anexo_numero=6
                   ORDER BY s6.es_actual DESC, s6.revision DESC, s6.fecha_presentacion DESC
                   LIMIT 1
               ) AS anexo6_json,
               (
                   SELECT s5.contenido_json
                   FROM rsu_annex_submission s5
                   WHERE s5.proyecto_id=p.proyecto_id AND s5.anexo_numero=5
                   ORDER BY s5.es_actual DESC, s5.revision DESC, s5.fecha_presentacion DESC
                   LIMIT 1
               ) AS anexo5_json
        FROM proyecto p
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        {extra_joins}
        LEFT JOIN (
            SELECT proyecto_id, COUNT(*) AS cantidad
            FROM rol_proyecto
            WHERE activo=1
            GROUP BY proyecto_id
        ) participantes ON participantes.proyecto_id=p.proyecto_id
        LEFT JOIN (
            SELECT proyecto_id, SUM(GREATEST(COALESCE(horas_certificadas,0),0)) AS horas
            FROM certificado
            WHERE fecha_emision IS NOT NULL
            GROUP BY proyecto_id
        ) certificados ON certificados.proyecto_id=p.proyecto_id
        LEFT JOIN (
            SELECT rp_j.proyecto_id, SUM(GREATEST(COALESCE(j.horas,0),0)) AS horas
            FROM jornada_voluntariado j
            INNER JOIN rol_proyecto rp_j ON rp_j.rol_proyecto_id=j.rol_proyecto_id
            WHERE rp_j.activo=1
            GROUP BY rp_j.proyecto_id
        ) jornadas ON jornadas.proyecto_id=p.proyecto_id
        LEFT JOIN (
            SELECT proyecto_id, SUM(GREATEST(COALESCE(horas_acumuladas,0),0)) AS horas
            FROM rol_proyecto
            WHERE activo=1
            GROUP BY proyecto_id
        ) legado ON legado.proyecto_id=p.proyecto_id
        LEFT JOIN rsu_specific_activity_workflow saw ON saw.proyecto_id=p.proyecto_id
        WHERE {where_sql}
        """,
        tuple(params or ()),
    )

    total = 0.0
    for row in rows:
        certified = _number(row.get("horas_certificadas"))
        if certified > 0:
            total += certified
            continue

        module = str(row.get("modulo") or "proyectos")
        legacy = _number(row.get("horas_legado"))
        participants = max(int(_number(row.get("participantes"))), 0)

        if module == "voluntariado":
            total += _number(row.get("horas_jornadas")) or legacy
            continue

        if module == "actividades":
            state = str(row.get("estado") or "")
            final_or_reported = any(
                token in state
                for token in ("cierre", "final", "certific", "aprobada", "aprobado")
            )
            activity_hours = _number(row.get("horas_actividad"))
            if final_or_reported and activity_hours > 0 and participants > 0:
                total += activity_hours * participants
            else:
                total += legacy
            continue

        duration = _json_hours(row.get("anexo6_json")) or _json_hours(row.get("anexo5_json"))
        if duration > 0 and participants > 0:
            total += duration * participants
        else:
            total += legacy

    return round(total, 1)
