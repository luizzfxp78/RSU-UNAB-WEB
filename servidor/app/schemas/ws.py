from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel


class WsEvent(BaseModel):
    type: str
    message: str
    data: Optional[Dict[str, Any]] = None
    created_at: datetime = datetime.utcnow()
