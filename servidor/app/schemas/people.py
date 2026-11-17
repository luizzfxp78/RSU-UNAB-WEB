from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.core.password_policy import validate_password_policy


class PersonaContactoIn(BaseModel):
    nombres: str = Field(min_length=1, max_length=100)
    apellidos: str = Field(min_length=1, max_length=100)
    dni: str = Field(min_length=8, max_length=8, pattern=r"^\d{8}$")
    activo: bool = True
    email_personal: Optional[str] = Field(default=None, max_length=150)
    email_institucional: Optional[str] = Field(default=None, max_length=150)
    telefono: Optional[str] = Field(default=None, max_length=15)
    direccion: Optional[str] = Field(default=None, max_length=255)


class PersonaContactoOut(PersonaContactoIn):
    persona_id: str
    contacto_id: Optional[str] = None


class PersonaRegistroBase(PersonaContactoIn):
    """Datos comunes para registrar una persona y, opcionalmente, su cuenta."""

    crear_cuenta: bool = True
    username: Optional[str] = Field(default=None, min_length=3, max_length=50)
    password: Optional[str] = Field(default=None, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: Optional[str]) -> Optional[str]:
        if value not in (None, ""):
            validate_password_policy(value)
        return value


class AdministrativoRegistroIn(PersonaRegistroBase):
    area: Optional[str] = Field(default=None, max_length=100)
    cargo: Optional[str] = Field(default=None, max_length=100)
    rol_sistema: Optional[Literal[
        "administrador_rsu",
        "administrativo_normal",
        "director_rsu",
        "director_facultad",
        "vicepresidente_academico",
    ]] = None
    facultad_asignada: Optional[str] = Field(default=None, max_length=150)
    cargo_oficial: Optional[str] = Field(default=None, max_length=150)
    oficina: Optional[str] = Field(default=None, max_length=180)
    resolucion_designacion: Optional[str] = Field(default=None, max_length=100)
    fecha_inicio_designacion: Optional[str] = None
    fecha_fin_designacion: Optional[str] = None
    firma_base64: Optional[str] = None
    firma_filename: Optional[str] = Field(default=None, max_length=255)


class DocenteRegistroIn(PersonaRegistroBase):
    codigo_docente: str = Field(min_length=1, max_length=20)
    departamento_academico_id: str = Field(min_length=1, max_length=36)
    especialidad: Optional[str] = Field(default=None, max_length=150)
    condicion: Optional[str] = Field(default=None, max_length=50)
    categoria: Optional[str] = Field(default=None, max_length=50)
    regimen_dedicacion: Optional[str] = Field(default=None, max_length=50)


class EstudianteRegistroIn(PersonaRegistroBase):
    codigo_estudiante: str = Field(min_length=1, max_length=20)
    departamento_academico_id: str = Field(min_length=1, max_length=36)
    es_becado: bool = False


class VinculadoRegistroIn(PersonaRegistroBase):
    tipo_vinculo: Literal["no_docente", "egresado", "graduado"]
    departamento_academico_id: Optional[str] = Field(default=None, max_length=36)
    codigo_institucional: Optional[str] = Field(default=None, max_length=30)
    anio_egreso: Optional[int] = Field(default=None, ge=1900, le=2100)
    grado_academico: Optional[str] = Field(default=None, max_length=120)
    institucion_origen: Optional[str] = Field(default=None, max_length=180)


class PersonaGestionUpdateIn(PersonaContactoIn):
    """Edición completa desde el módulo Gestión de personal.

    Permite cambiar el tipo de persona. El servidor crea o actualiza la tabla
    específica del nuevo rol y elimina los datos del rol anterior que ya no
    correspondan.
    """

    tipo_persona: Literal["administrativo", "docente", "estudiante"]

    # Cuenta de acceso. Desmarcar crear_cuenta conserva la cuenta, pero la bloquea.
    crear_cuenta: bool = False
    username: Optional[str] = Field(default=None, max_length=50)
    password: Optional[str] = Field(default=None, max_length=128)
    usuario_activo: bool = True

    # Administrativo
    area: Optional[str] = Field(default=None, max_length=100)
    cargo: Optional[str] = Field(default=None, max_length=100)

    # Docente
    codigo_docente: Optional[str] = Field(default=None, max_length=20)
    departamento_academico_id: Optional[str] = Field(default=None, max_length=36)
    especialidad: Optional[str] = Field(default=None, max_length=150)
    condicion: Optional[str] = Field(default=None, max_length=50)
    categoria: Optional[str] = Field(default=None, max_length=50)
    regimen_dedicacion: Optional[str] = Field(default=None, max_length=50)

    # Estudiante
    codigo_estudiante: Optional[str] = Field(default=None, max_length=20)
    es_becado: bool = False

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: Optional[str]) -> Optional[str]:
        if value not in (None, ""):
            validate_password_policy(value)
        return value


class PersonaEstadoIn(BaseModel):
    activo: bool
