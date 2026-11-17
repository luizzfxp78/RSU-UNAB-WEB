from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from app.db.mysql import execute, fetch_all, fetch_one
from app.services.notification_service import (
    eligible_call_user_ids,
    notify_project_responsibles,
    notify_users,
)

logger = logging.getLogger(__name__)


async def process_notification_reminders() -> None:
    """Genera recordatorios y aplica vencimientos institucionales."""
    today = date.today()
    warning_date = today + timedelta(days=2)

    # Las actividades específicas observadas solo disponen de tres días hábiles.
    # El proceso horario evita que dependan de que alguien vuelva a abrir el expediente.
    expired_activities = fetch_all(
        """
        SELECT w.proyecto_id,p.titulo,w.etapa_actual,w.fecha_limite_subsanacion
        FROM rsu_specific_activity_workflow w
        INNER JOIN proyecto p ON p.proyecto_id=w.proyecto_id
        WHERE w.estado_flujo IN ('actividad_observada','actividad_cierre_observado')
          AND w.fecha_limite_subsanacion IS NOT NULL
          AND w.fecha_limite_subsanacion < %s
        """,
        (today,),
    )
    not_presented = fetch_one(
        "SELECT catalogo_estado_id FROM catalogo_estado WHERE entidad='proyecto' AND LOWER(nombre)='actividad_no_presentada' LIMIT 1"
    )
    for activity in expired_activities:
        project_id = str(activity.get("proyecto_id") or "")
        execute(
            """UPDATE rsu_specific_activity_workflow
               SET estado_flujo='actividad_no_presentada',etapa_actual='finalizada',
                   motivo_fin='Plazo de subsanación vencido',fecha_limite_subsanacion=NULL
               WHERE proyecto_id=%s""",
            (project_id,),
        )
        if not_presented:
            execute(
                "UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s",
                (not_presented.get("catalogo_estado_id"), project_id),
            )
        await notify_project_responsibles(
            project_id,
            notification_type="actividad_plazo_vencido",
            title="Actividad considerada no presentada",
            message=(
                f"Venció el plazo de subsanación de la actividad «{activity.get('titulo')}». "
                "El procedimiento ha finalizado como no presentado."
            ),
            module="Actividades específicas",
            priority="alta",
            entity_type="actividad_especifica",
            entity_id=project_id,
            data={"proyecto_id": project_id, "pantalla": "actividades"},
            event_key=f"actividad:{project_id}:plazo_vencido:{activity.get('fecha_limite_subsanacion')}",
            important=True,
        )

    calls = fetch_all(
        """
        SELECT cv.convocatoria_id, cv.fecha_limite_presentacion,
               tp.nombre AS tipo_proyecto, pa.nombre AS periodo_academico
        FROM convocatoria cv
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        WHERE LOWER(ce.nombre)='abierta'
          AND cv.fecha_limite_presentacion BETWEEN %s AND %s
        """,
        (today, warning_date),
    )
    recipients = eligible_call_user_ids()
    for call in calls:
        deadline = call.get("fecha_limite_presentacion")
        await notify_users(
            recipients,
            notification_type="recordatorio_convocatoria",
            title="Recordatorio de convocatoria",
            message=(
                f"La convocatoria {call.get('tipo_proyecto')} - {call.get('periodo_academico')} "
                f"cierra el {deadline}."
            ),
            module="Módulo de Convocatorias",
            priority="alta",
            entity_type="convocatoria",
            entity_id=str(call.get("convocatoria_id")),
            data={
                "convocatoria_id": call.get("convocatoria_id"),
                "fecha_limite_presentacion": deadline,
                "pantalla": "convocatorias",
            },
            event_key=f"convocatoria:{call.get('convocatoria_id')}:recordatorio:{deadline}",
            important=True,
        )

    projects = fetch_all(
        """
        SELECT w.proyecto_id, w.fecha_limite_actual, w.etapa_actual, p.titulo
        FROM rsu_project_workflow w
        INNER JOIN proyecto p ON p.proyecto_id=w.proyecto_id
        WHERE w.eliminado_logico=0
          AND w.fecha_limite_actual BETWEEN %s AND %s
          AND w.estado_flujo NOT IN ('finalizado','desaprobado','no_presentado')
        """,
        (today, warning_date),
    )
    for project in projects:
        deadline = project.get("fecha_limite_actual")
        await notify_project_responsibles(
            str(project.get("proyecto_id")),
            notification_type="recordatorio_plazo",
            title="Recordatorio de plazo",
            message=(
                f"El plazo de la etapa «{project.get('etapa_actual')}» del proyecto "
                f"«{project.get('titulo')}» vence el {deadline}."
            ),
            module="Sistema RSU",
            priority="alta",
            entity_type="proyecto",
            entity_id=str(project.get("proyecto_id")),
            data={
                "proyecto_id": project.get("proyecto_id"),
                "titulo_proyecto": project.get("titulo"),
                "fecha_limite": deadline,
                "pantalla": "proyectos",
            },
            event_key=f"proyecto:{project.get('proyecto_id')}:recordatorio:{deadline}:{project.get('etapa_actual')}",
            important=True,
        )


async def notification_reminder_loop() -> None:
    # Revisión inmediata al iniciar y luego una vez por hora.
    while True:
        try:
            await process_notification_reminders()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Falló el procesamiento de recordatorios RSU")
        await asyncio.sleep(3600)
