from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder

from app.db.mysql import execute, fetch_all, fetch_one
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    raise TypeError(f"No serializable: {type(value)!r}")


def administrative_user_ids() -> list[str]:
    try:
        rows = fetch_all(
            """
            SELECT DISTINCT u.usuario_id
            FROM usuario u
            INNER JOIN persona p ON p.persona_id=u.persona_id AND p.activo=1
            INNER JOIN rsu_user_system_role usr ON usr.usuario_id=u.usuario_id AND usr.activo=1
            INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id AND sr.activo=1
            WHERE u.activo=1 AND sr.codigo IN ('administrador_rsu','director_rsu')
            """
        )
    except Exception:  # noqa: BLE001
        logger.exception("No se pudieron resolver los destinatarios administrativos")
        return []
    return [str(row["usuario_id"]) for row in rows]


def eligible_call_user_ids() -> list[str]:
    try:
        rows = fetch_all(
            """
            SELECT DISTINCT u.usuario_id
            FROM usuario u
            INNER JOIN persona p ON p.persona_id=u.persona_id AND p.activo=1
            LEFT JOIN docente d ON d.persona_id=p.persona_id
            LEFT JOIN estudiante e ON e.persona_id=p.persona_id
            LEFT JOIN rsu_user_system_role usr ON usr.usuario_id=u.usuario_id AND usr.activo=1
            LEFT JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id AND sr.activo=1
            WHERE u.activo=1 AND (
                d.persona_id IS NOT NULL OR e.persona_id IS NOT NULL
                OR sr.codigo='administrativo_normal'
            )
            """
        )
    except Exception:  # noqa: BLE001
        logger.exception("No se pudieron resolver los usuarios habilitados para convocatorias")
        return []
    return [str(row["usuario_id"]) for row in rows]


def project_user_ids(project_id: str, roles: Iterable[str] | None = None) -> list[str]:
    params: list[Any] = [project_id]
    role_sql = ""
    clean_roles = [str(role).strip().lower() for role in (roles or []) if str(role).strip()]
    if clean_roles:
        placeholders = ",".join(["%s"] * len(clean_roles))
        role_sql = f" AND LOWER(cr.nombre) IN ({placeholders})"
        params.extend(clean_roles)
    try:
        rows = fetch_all(
            f"""
            SELECT DISTINCT u.usuario_id
            FROM rol_proyecto rp
            INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
            INNER JOIN usuario u ON u.persona_id=rp.persona_id AND u.activo=1
            INNER JOIN persona p ON p.persona_id=u.persona_id AND p.activo=1
            WHERE rp.proyecto_id=%s AND rp.activo=1 {role_sql}
            """,
            tuple(params),
        )
    except Exception:  # noqa: BLE001
        logger.exception("No se pudieron resolver los destinatarios del proyecto %s", project_id)
        return []
    return [str(row["usuario_id"]) for row in rows]


def _project_title(project_id: str | None, data: dict[str, Any] | None = None) -> str:
    supplied = str((data or {}).get("titulo_proyecto") or "").strip()
    if supplied:
        return supplied
    if not project_id:
        return ""
    try:
        row = fetch_one("SELECT titulo FROM proyecto WHERE proyecto_id=%s LIMIT 1", (project_id,)) or {}
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo resolver el título del proyecto %s", project_id)
        return ""
    return str(row.get("titulo") or "").strip()


def _standard_project_notification_title(
    title: str,
    *,
    project_id: str | None,
    data: dict[str, Any] | None,
) -> str:
    """Formato visible: Nombre del proyecto: evento / estado."""
    project_title = _project_title(project_id, data)
    event_title = str(title or "Notificación").strip()
    if not project_title:
        return event_title
    if event_title.casefold().startswith(project_title.casefold() + ":"):
        return event_title
    return f"{project_title}: {event_title}"


def notification_counts(user_id: str) -> dict[str, int]:
    row = fetch_one(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN leida=0 AND archivada=0 THEN 1 ELSE 0 END) AS no_leidas,
               SUM(CASE WHEN importante=1 AND archivada=0 THEN 1 ELSE 0 END) AS importantes,
               SUM(CASE WHEN archivada=1 THEN 1 ELSE 0 END) AS archivadas
        FROM rsu_notification
        WHERE usuario_id=%s
        """,
        (user_id,),
    ) or {}
    return {
        "total": int(row.get("total") or 0),
        "no_leidas": int(row.get("no_leidas") or 0),
        "importantes": int(row.get("importantes") or 0),
        "archivadas": int(row.get("archivadas") or 0),
    }


async def notify_users(
    user_ids: Iterable[str],
    *,
    notification_type: str,
    title: str,
    message: str,
    module: str = "Sistema",
    priority: str = "informativa",
    entity_type: str | None = None,
    entity_id: str | None = None,
    project_id: str | None = None,
    annex_number: int | None = None,
    data: dict[str, Any] | None = None,
    event_key: str | None = None,
    generated_by: str = "Sistema RSU",
    important: bool | None = None,
) -> list[dict[str, Any]]:
    """Guarda primero y luego intenta entregar en tiempo real.

    Un fallo del canal WebSocket nunca revierte la operación principal que originó
    la alerta. La clave de evento impide duplicados por usuario.
    """
    payload_data = dict(data or {})
    title = _standard_project_notification_title(title, project_id=project_id, data=payload_data)
    created: list[dict[str, Any]] = []
    clean_users = list(dict.fromkeys(str(item) for item in user_ids if item))
    for user_id in clean_users:
        notification_id = str(uuid4())
        unique_key = f"{event_key}:usuario:{user_id}" if event_key else None
        record = {
            "notificacion_id": notification_id,
            "usuario_id": user_id,
            "tipo": notification_type,
            "titulo": title,
            "mensaje": message,
            "modulo": module,
            "prioridad": priority,
            "entidad_tipo": entity_type,
            "entidad_id": entity_id,
            "proyecto_id": project_id,
            "anexo_numero": annex_number,
            "datos": payload_data,
            "clave_evento": unique_key,
            "generada_por": generated_by,
            "canal": "Aplicación",
            "leida": False,
            "importante": bool(important if important is not None else priority == "alta"),
            "archivada": False,
            "fecha_creacion": datetime.now(),
        }
        try:
            affected = execute(
                """
                INSERT IGNORE INTO rsu_notification
                (notificacion_id,usuario_id,tipo,titulo,mensaje,modulo,prioridad,
                 entidad_tipo,entidad_id,proyecto_id,anexo_numero,datos_json,
                 clave_evento,generada_por,canal,leida,importante,archivada,fecha_creacion)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,0,%s)
                """,
                (
                    notification_id,
                    user_id,
                    notification_type,
                    title,
                    message,
                    module,
                    priority,
                    entity_type,
                    entity_id,
                    project_id,
                    annex_number,
                    json.dumps(payload_data, ensure_ascii=False, default=_json_default),
                    unique_key,
                    generated_by,
                    "Aplicación",
                    int(record["importante"]),
                    record["fecha_creacion"],
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo guardar la notificación %s", notification_id)
            continue
        if affected <= 0:
            continue
        created.append(record)

    for record in created:
        try:
            await ws_manager.send_to_user(
                record["usuario_id"],
                jsonable_encoder({"type": "notificacion.nueva", "data": record}),
            )
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo entregar la notificación %s por WebSocket", record["notificacion_id"])
    return created


async def notify_admins(**kwargs: Any) -> list[dict[str, Any]]:
    return await notify_users(administrative_user_ids(), **kwargs)


async def notify_project_responsibles(project_id: str, **kwargs: Any) -> list[dict[str, Any]]:
    return await notify_users(
        project_user_ids(project_id, roles=["responsable", "corresponsable"]),
        project_id=project_id,
        **kwargs,
    )
