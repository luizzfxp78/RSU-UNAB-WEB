from __future__ import annotations

from pydantic import BaseModel


class NotificationBooleanUpdate(BaseModel):
    valor: bool = True
