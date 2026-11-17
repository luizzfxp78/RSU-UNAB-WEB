from __future__ import annotations

from typing import Any


def project_period_filter(
    period_id: str | None,
    project_alias: str = "p",
    call_alias: str = "cv",
) -> tuple[str, list[Any]]:
    """Filtra un ciclo y agrega los expedientes del periodo anual del mismo año.

    Cuando se selecciona, por ejemplo, 2026-I, también se incluyen proyectos
    vinculados a 2026-ANUAL únicamente si sus fechas se superponen con el rango
    de inicio y fin del ciclo 2026-I. Los periodos seleccionados como "Todos"
    se resuelven antes de invocar esta función.
    """
    if not period_id:
        return "1=1", []

    project_period = f"COALESCE({project_alias}.periodo_academico_id,{call_alias}.periodo_academico_id)"
    project_start = (
        f"DATE(COALESCE({project_alias}.fecha_inicio,{project_alias}.fecha_presentacion,"
        f"{project_alias}.fecha_creacion,{call_alias}.fecha_inicio))"
    )
    project_end = (
        f"DATE(COALESCE({project_alias}.fecha_fin,{project_alias}.fecha_presentacion,"
        f"{project_alias}.fecha_creacion,{call_alias}.fecha_limite_presentacion))"
    )
    return (
        f"""(
            {project_period}=%s
            OR EXISTS (
                SELECT 1
                FROM periodo_academico pa_seleccionado
                INNER JOIN periodo_academico pa_proyecto
                    ON pa_proyecto.periodo_academico_id={project_period}
                WHERE pa_seleccionado.periodo_academico_id=%s
                  AND LOWER(COALESCE(pa_seleccionado.nombre,'')) NOT LIKE '%%anual%%'
                  AND LOWER(COALESCE(pa_proyecto.nombre,'')) LIKE '%%anual%%'
                  AND YEAR(pa_proyecto.fecha_inicio)=YEAR(pa_seleccionado.fecha_inicio)
                  AND {project_start}<=pa_seleccionado.fecha_fin
                  AND {project_end}>=pa_seleccionado.fecha_inicio
            )
        )""",
        [period_id, period_id],
    )


def convocation_period_filter(
    period_id: str | None,
    call_alias: str = "cv",
) -> tuple[str, list[Any]]:
    """Aplica al catálogo de convocatorias la misma regla ciclo + anual."""
    if not period_id:
        return "1=1", []
    return (
        f"""(
            {call_alias}.periodo_academico_id=%s
            OR EXISTS (
                SELECT 1
                FROM periodo_academico pa_seleccionado
                INNER JOIN periodo_academico pa_convocatoria
                    ON pa_convocatoria.periodo_academico_id={call_alias}.periodo_academico_id
                WHERE pa_seleccionado.periodo_academico_id=%s
                  AND LOWER(COALESCE(pa_seleccionado.nombre,'')) NOT LIKE '%%anual%%'
                  AND LOWER(COALESCE(pa_convocatoria.nombre,'')) LIKE '%%anual%%'
                  AND YEAR(pa_convocatoria.fecha_inicio)=YEAR(pa_seleccionado.fecha_inicio)
                  AND {call_alias}.fecha_inicio<=pa_seleccionado.fecha_fin
                  AND {call_alias}.fecha_limite_presentacion>=pa_seleccionado.fecha_inicio
            )
        )""",
        [period_id, period_id],
    )
