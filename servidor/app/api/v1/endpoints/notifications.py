from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.db.mysql import execute, fetch_all, fetch_one
from app.schemas.auth import AuthUser
from app.schemas.notifications import NotificationBooleanUpdate
from app.services.notification_service import notification_counts

router = APIRouter(prefix="/notificaciones", tags=["Notificaciones persistentes"])


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    raw = item.pop("datos_json", None)
    if isinstance(raw, dict):
        item["datos"] = raw
    else:
        try:
            item["datos"] = json.loads(raw or "{}")
        except (TypeError, ValueError):
            item["datos"] = {}
    project_title = str(item.pop("titulo_proyecto", "") or "").strip()
    current_title = str(item.get("titulo") or "Notificación").strip()
    if project_title and not current_title.casefold().startswith(project_title.casefold() + ":"):
        item["titulo"] = f"{project_title}: {current_title}"
    for key in ("leida", "importante", "archivada"):
        item[key] = bool(item.get(key))
    return item


def _base_where(user_id: str, *, tab: str, search: str, kind: str, date_filter: str, state: str) -> tuple[str, list[Any]]:
    clauses = ["n.usuario_id=%s"]
    params: list[Any] = [user_id]
    tab_value = tab.strip().lower()
    if tab_value == "no_leidas":
        clauses.extend(["n.leida=0", "n.archivada=0"])
    elif tab_value == "importantes":
        clauses.extend(["n.importante=1", "n.archivada=0"])
    elif tab_value == "archivadas":
        clauses.append("n.archivada=1")
    else:
        clauses.append("n.archivada=0")
    if search.strip():
        like = f"%{search.strip()}%"
        clauses.append("(n.titulo LIKE %s OR n.mensaje LIKE %s OR n.modulo LIKE %s OR p.titulo LIKE %s)")
        params.extend([like, like, like, like])
    if kind.strip() and kind.strip().lower() != "todos":
        clauses.append("LOWER(n.tipo)=LOWER(%s)")
        params.append(kind.strip())
    if state == "leidas":
        clauses.append("n.leida=1")
    elif state == "no_leidas":
        clauses.append("n.leida=0")
    if date_filter == "hoy":
        clauses.append("DATE(n.fecha_creacion)=CURDATE()")
    elif date_filter == "semana":
        clauses.append("n.fecha_creacion >= DATE_SUB(NOW(), INTERVAL 7 DAY)")
    elif date_filter == "mes":
        clauses.append("n.fecha_creacion >= DATE_SUB(NOW(), INTERVAL 30 DAY)")
    return " AND ".join(clauses), params


@router.get("")
def list_notifications(
    tab: str = Query("todas"),
    search: str = Query(""),
    kind: str = Query("todos"),
    date_filter: str = Query("todas"),
    state: str = Query("todos"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    user: AuthUser = Depends(get_current_user),
):
    where, params = _base_where(user.usuario_id, tab=tab, search=search, kind=kind, date_filter=date_filter, state=state)
    total_row = fetch_one(f"SELECT COUNT(*) AS total FROM rsu_notification n LEFT JOIN proyecto p ON p.proyecto_id=n.proyecto_id WHERE {where}", tuple(params)) or {"total": 0}
    offset = (page - 1) * limit
    rows = fetch_all(
        f"""
        SELECT n.*, p.titulo AS titulo_proyecto
        FROM rsu_notification n
        LEFT JOIN proyecto p ON p.proyecto_id=n.proyecto_id
        WHERE {where}
        ORDER BY n.fecha_creacion DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, offset]),
    )
    return {
        "items": [_serialize(row) for row in rows],
        "total_filtrado": int(total_row.get("total") or 0),
        "page": page,
        "limit": limit,
        "counts": notification_counts(user.usuario_id),
    }


@router.get("/resumen")
def notification_center(user: AuthUser = Depends(get_current_user)):
    payload = list_notifications(tab="todas", search="", kind="todos", date_filter="todas", state="todos", page=1, limit=200, user=user)
    types = fetch_all(
        """
        SELECT tipo, COUNT(*) AS cantidad
        FROM rsu_notification
        WHERE usuario_id=%s
        GROUP BY tipo ORDER BY cantidad DESC, tipo
        """,
        (user.usuario_id,),
    )
    payload.update({"module": "notificaciones", "types": types})
    return payload


@router.get("/conteo")
def unread_count(user: AuthUser = Depends(get_current_user)):
    return notification_counts(user.usuario_id)


@router.patch("/{notification_id}/leer")
def mark_read(notification_id: str, payload: NotificationBooleanUpdate, user: AuthUser = Depends(get_current_user)):
    affected = execute(
        """
        UPDATE rsu_notification
        SET leida=%s, fecha_lectura=CASE WHEN %s=1 THEN NOW() ELSE NULL END
        WHERE notificacion_id=%s AND usuario_id=%s
        """,
        (int(payload.valor), int(payload.valor), notification_id, user.usuario_id),
    )
    if affected == 0:
        raise HTTPException(status_code=404, detail="Notificación no encontrada.")
    return {"ok": True, "counts": notification_counts(user.usuario_id)}


@router.patch("/{notification_id}/importante")
def mark_important(notification_id: str, payload: NotificationBooleanUpdate, user: AuthUser = Depends(get_current_user)):
    affected = execute(
        "UPDATE rsu_notification SET importante=%s WHERE notificacion_id=%s AND usuario_id=%s",
        (int(payload.valor), notification_id, user.usuario_id),
    )
    if affected == 0:
        raise HTTPException(status_code=404, detail="Notificación no encontrada.")
    return {"ok": True, "counts": notification_counts(user.usuario_id)}


@router.patch("/{notification_id}/archivar")
def archive(notification_id: str, payload: NotificationBooleanUpdate, user: AuthUser = Depends(get_current_user)):
    affected = execute(
        """
        UPDATE rsu_notification
        SET archivada=%s, fecha_archivo=CASE WHEN %s=1 THEN NOW() ELSE NULL END
        WHERE notificacion_id=%s AND usuario_id=%s
        """,
        (int(payload.valor), int(payload.valor), notification_id, user.usuario_id),
    )
    if affected == 0:
        raise HTTPException(status_code=404, detail="Notificación no encontrada.")
    return {"ok": True, "counts": notification_counts(user.usuario_id)}


@router.patch("/acciones/leer-todas")
def mark_all_read(user: AuthUser = Depends(get_current_user)):
    execute(
        "UPDATE rsu_notification SET leida=1, fecha_lectura=COALESCE(fecha_lectura,NOW()) WHERE usuario_id=%s AND archivada=0 AND leida=0",
        (user.usuario_id,),
    )
    return {"ok": True, "counts": notification_counts(user.usuario_id)}
