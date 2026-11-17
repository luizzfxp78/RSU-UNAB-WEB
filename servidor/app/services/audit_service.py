from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.db.mysql import execute, fetch_one


_WRITE_ACTION_WORDS = (
    "aprobar", "approve", "observar", "observe", "evaluar", "evaluate",
    "presentar", "submit", "subsanar", "correct", "iniciar", "start",
    "cerrar", "close", "activar", "deactivate", "desactivar", "certificar",
    "emitir", "asignar", "marcar", "archivar", "convalidar", "actualizar",
)


def classify_audit_action(method: str, path: str) -> str:
    method_key = str(method or "").upper()
    lower_path = str(path or "").lower()
    if method_key == "DELETE":
        return "ELIMINACIÓN"
    if method_key in {"PUT", "PATCH"}:
        return "EDICIÓN"
    if method_key == "POST" and any(word in lower_path for word in _WRITE_ACTION_WORDS):
        return "EDICIÓN"
    return "REGISTRO"


def module_from_path(path: str) -> str:
    value = str(path or "").lower()
    mappings = (
        ("/voluntariados", "Voluntariado RSU"),
        ("/actividades-especificas", "Actividades específicas"),
        ("/ciclo-rsu", "Proyectos RSU"),
        ("/flujo-rsu", "Flujo RSU"),
        ("/plantillas-anexos", "Plantillas de anexos"),
        ("/roles-autoridades", "Roles y autoridades"),
        ("/personas", "Gestión de personal"),
        ("/catalogos", "Catálogos"),
        ("/proyectos", "Proyectos"),
        ("/documentos", "Documentos"),
        ("/notificaciones", "Notificaciones"),
        ("/auth", "Autenticación"),
    )
    for prefix, label in mappings:
        if prefix in value:
            return label
    return "Sistema RSU"


def entity_from_path(path: str) -> tuple[str, str | None]:
    clean = str(path or "").split("?", 1)[0].strip("/")
    parts = [part for part in clean.split("/") if part]
    if parts[:2] == ["api", "v1"]:
        parts = parts[2:]
    entity = parts[0].replace("-", " ").title() if parts else "Sistema"
    resource_id = None
    for part in parts[1:]:
        if len(part) >= 20 and " " not in part:
            resource_id = part[:120]
            break
    return entity, resource_id


def record_audit_event(
    *,
    usuario_id: str | None,
    method: str,
    path: str,
    status_code: int,
    ip_address: str | None = None,
    user_agent: str | None = None,
    detail: str | None = None,
) -> None:
    """Registra una acción de escritura sin interrumpir la operación principal."""
    actor: dict[str, Any] = {}
    if usuario_id:
        actor = fetch_one(
            """
            SELECT u.usuario_id, u.persona_id, u.username,
                   CONCAT(p.nombres, ' ', p.apellidos) AS nombre_completo,
                   (
                     SELECT sr.codigo
                     FROM rsu_user_system_role usr
                     INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id
                     WHERE usr.usuario_id=u.usuario_id AND usr.activo=1 AND sr.activo=1
                     ORDER BY usr.fecha_asignacion DESC LIMIT 1
                   ) AS rol_sistema
            FROM usuario u
            INNER JOIN persona p ON p.persona_id=u.persona_id
            WHERE u.usuario_id=%s
            LIMIT 1
            """,
            (usuario_id,),
        ) or {}
    entity, resource_id = entity_from_path(path)
    execute(
        """
        INSERT INTO rsu_audit_log (
          audit_id, accion, modulo, entidad, recurso_id, metodo_http, ruta,
          detalle, estado_http, usuario_id, persona_id, usuario_responsable,
          username, rol_sistema, direccion_ip, user_agent, fecha_hora
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(6))
        """,
        (
            str(uuid4()),
            classify_audit_action(method, path),
            module_from_path(path),
            entity,
            resource_id,
            str(method or "").upper(),
            str(path or "")[:500],
            str(detail or "")[:500] or None,
            int(status_code),
            actor.get("usuario_id"),
            actor.get("persona_id"),
            str(actor.get("nombre_completo") or "Sistema")[:180],
            actor.get("username"),
            actor.get("rol_sistema"),
            str(ip_address or "")[:64] or None,
            str(user_agent or "")[:300] or None,
        ),
    )
