from typing import Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1)


class AuthUser(BaseModel):
    usuario_id: str
    persona_id: str
    username: str
    nombre_completo: str
    dni: str
    tipo_usuario: str
    email_institucional: Optional[str] = None
    detalle: Optional[str] = None
    rol_sistema: Optional[str] = None
    facultad_asignada: Optional[str] = None
    domicilio: Optional[str] = None
    condicion: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUser
