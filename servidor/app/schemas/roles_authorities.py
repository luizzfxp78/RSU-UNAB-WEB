from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.core.password_policy import validate_password_policy


class SystemRoleIn(BaseModel):
    codigo: str = Field(min_length=3, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    nombre: str = Field(min_length=3, max_length=120)
    descripcion: Optional[str] = Field(default=None, max_length=255)
    activo: bool = True

    @field_validator("codigo")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().lower()


class AuthorityAssignmentIn(BaseModel):
    persona_id: str = Field(min_length=1, max_length=36)
    system_role_id: str = Field(min_length=1, max_length=36)
    facultad: Optional[str] = Field(default=None, max_length=150)
    cargo_oficial: str = Field(min_length=3, max_length=150)
    oficina: Optional[str] = Field(default=None, max_length=180)
    resolucion_designacion: Optional[str] = Field(default=None, max_length=100)
    fecha_inicio: Optional[date] = None
    fecha_fin: Optional[date] = None
    firma_base64: Optional[str] = None
    firma_filename: Optional[str] = Field(default=None, max_length=255)
    crear_o_activar_cuenta: bool = False
    username: Optional[str] = Field(default=None, max_length=50)
    password: Optional[str] = Field(default=None, max_length=128)
    activo: bool = True

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: Optional[str]) -> Optional[str]:
        if value not in (None, ""):
            validate_password_policy(value)
        return value


class AuthorityAssignmentStatusIn(BaseModel):
    activo: bool
