from __future__ import annotations

import logging

from app.core.security import is_bcrypt_hash
from app.db.mysql import fetch_all

logger = logging.getLogger(__name__)


def audit_stored_password_hashes() -> dict[str, int]:
    """Comprueba que las credenciales existentes sean hashes bcrypt válidos.

    No muestra ni modifica contraseñas. Las cuentas con valores heredados o
    dañados se rechazan al iniciar sesión y deben recibir una contraseña nueva.
    """
    rows = fetch_all("SELECT password_hash FROM usuario")
    total = len(rows)
    invalid = sum(1 for row in rows if not is_bcrypt_hash(str(row.get("password_hash") or "")))
    if invalid:
        logger.warning(
            "Se detectaron %s de %s cuentas con un valor de contraseña que no es un hash bcrypt válido. "
            "Esas cuentas deben restablecer su contraseña.",
            invalid,
            total,
        )
    else:
        logger.info("Verificación de contraseñas: %s cuentas almacenadas con hash bcrypt válido.", total)
    return {"total": total, "invalid": invalid}
