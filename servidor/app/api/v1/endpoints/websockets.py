from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import decode_token
from app.services.websocket_manager import ws_manager

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return

    try:
        payload = decode_token(token)
    except Exception:  # noqa: BLE001
        await websocket.close(code=1008)
        return

    user_id = str(payload.get("sub"))
    await ws_manager.connect(
        user_id,
        websocket,
        system_role=str(payload.get("rol_sistema") or ""),
        user_type=str(payload.get("tipo_usuario") or ""),
    )
    await websocket.send_json({"type": "connection.ok", "message": "Conectado a notificaciones RSU."})
    try:
        while True:
            # Mantiene viva la conexión. La app puede enviar {"type":"ping"}.
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id, websocket)
