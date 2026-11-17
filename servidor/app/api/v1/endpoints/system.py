from fastapi import APIRouter

from app.db.mysql import fetch_one
from app.services.websocket_manager import ws_manager

router = APIRouter(tags=["Sistema"])


@router.get("/health")
def health():
    db = fetch_one("SELECT 1 AS ok")
    return {"ok": True, "database": bool(db and db["ok"] == 1), "websocket_clients": ws_manager.count()}
