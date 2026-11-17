from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import re

import bcrypt
import jwt
from fastapi import HTTPException, status

from app.core.config import get_settings
from app.core.password_policy import validate_password_policy

ALGORITHM = "HS256"
_BCRYPT_HASH_PATTERN = re.compile(r"^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")


def is_bcrypt_hash(value: str | None) -> bool:
    return bool(_BCRYPT_HASH_PATTERN.fullmatch(str(value or "")))


def verify_password(plain_password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False

    if not is_bcrypt_hash(stored_hash):
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), stored_hash.encode("utf-8"))
    except (TypeError, ValueError):
        # Los valores simulados o dañados ya no se aceptan como contraseñas válidas.
        return False


def hash_password(plain_password: str) -> str:
    validated = validate_password_policy(plain_password)
    return bcrypt.hashpw(validated.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def create_access_token(subject: str, extra_claims: Optional[Dict[str, Any]] = None) -> str:
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: Dict[str, Any] = {"sub": subject, "exp": expires}
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión expirada.") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido.") from exc
