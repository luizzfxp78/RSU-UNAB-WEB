from typing import Dict, List

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: Dict[str, List[WebSocket]] = {}
        self._user_context: Dict[str, dict[str, str]] = {}

    async def connect(
        self,
        user_id: str,
        websocket: WebSocket,
        *,
        system_role: str = "",
        user_type: str = "",
    ) -> None:
        await websocket.accept()
        self._connections.setdefault(user_id, []).append(websocket)
        self._user_context[user_id] = {
            "system_role": str(system_role or "").strip().lower(),
            "user_type": str(user_type or "").strip().lower(),
        }

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        sockets = self._connections.get(user_id, [])
        if websocket in sockets:
            sockets.remove(websocket)
        if not sockets and user_id in self._connections:
            del self._connections[user_id]
            self._user_context.pop(user_id, None)

    async def send_to_user(self, user_id: str, payload: dict) -> None:
        dead: List[WebSocket] = []
        for ws in self._connections.get(user_id, []):
            try:
                await ws.send_json(jsonable_encoder(payload))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)

    @staticmethod
    def _matches_audience(context: dict[str, str], audience: list[str]) -> bool:
        if not audience:
            return True
        normalized = {str(item or "").strip().lower() for item in audience}
        role = context.get("system_role", "")
        user_type = context.get("user_type", "")
        if "todos" in normalized or "all" in normalized:
            return True
        if "administrativo" in normalized:
            if role:
                return role in {"administrador_rsu", "director_rsu", "director_facultad"}
            return user_type in {"administrador", "admin"}
        return role in normalized or user_type in normalized

    async def broadcast(self, payload: dict) -> None:
        audience = payload.get("audience") or []
        for user_id in list(self._connections.keys()):
            if self._matches_audience(self._user_context.get(user_id, {}), list(audience)):
                await self.send_to_user(user_id, payload)

    def count(self) -> int:
        return sum(len(items) for items in self._connections.values())


ws_manager = WebSocketManager()
