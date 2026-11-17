from __future__ import annotations

from dataclasses import dataclass

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_UTF8_BYTES = 72  # Límite efectivo de bcrypt.
PASSWORD_POLICY_MESSAGE = (
    "La contraseña debe tener al menos 8 caracteres e incluir por lo menos "
    "una letra mayúscula, un número y un signo especial."
)


@dataclass(frozen=True)
class PasswordPolicyResult:
    valid: bool
    message: str = ""


def check_password_policy(password: str | None) -> PasswordPolicyResult:
    value = str(password or "")
    missing: list[str] = []

    if len(value) < PASSWORD_MIN_LENGTH:
        missing.append("8 caracteres")
    if not any(character.isupper() for character in value):
        missing.append("una letra mayúscula")
    if not any(character.isdigit() for character in value):
        missing.append("un número")
    if not any((not character.isalnum()) and (not character.isspace()) for character in value):
        missing.append("un signo especial")

    if len(value.encode("utf-8")) > PASSWORD_MAX_UTF8_BYTES:
        return PasswordPolicyResult(
            False,
            "La contraseña es demasiado larga para el algoritmo de seguridad; "
            "usa como máximo 72 bytes en UTF-8.",
        )

    if missing:
        return PasswordPolicyResult(False, "La contraseña debe incluir " + ", ".join(missing) + ".")
    return PasswordPolicyResult(True)


def validate_password_policy(password: str | None) -> str:
    result = check_password_policy(password)
    if not result.valid:
        raise ValueError(result.message or PASSWORD_POLICY_MESSAGE)
    return str(password)
