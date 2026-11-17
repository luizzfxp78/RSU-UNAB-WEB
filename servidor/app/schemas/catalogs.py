from typing import Optional
from pydantic import BaseModel, Field


class EstadoOut(BaseModel):
    catalogo_estado_id: str
    entidad: str
    nombre: str
    descripcion: Optional[str] = None
    activo: bool


class LineaAccionIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=10)
    nombre: str = Field(min_length=1, max_length=150)
    descripcion: Optional[str] = None
    activo: bool = True


class TipoProyectoIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=100)
    horas_minimas: int = Field(ge=0)
    horas_jornada_minima: Optional[int] = Field(default=None, ge=0)
    descripcion: Optional[str] = None


class CatalogoRolIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=50)
    descripcion: Optional[str] = None
    solo_docente: bool = False
    solo_voluntariado: bool = False
    activo: bool = True


class TipoDocumentoIn(BaseModel):
    tipo_proyecto_id: str
    nombre: str = Field(min_length=1, max_length=100)
    anexo_referencia: Optional[str] = Field(default=None, max_length=10)
    etapa: str = Field(min_length=1, max_length=50)
    obligatorio: bool = True
    descripcion: Optional[str] = None


class DepartamentoAcademicoIn(BaseModel):
    facultad: str = Field(min_length=1, max_length=100)
    carrera: str = Field(min_length=1, max_length=150)
    codigo: str = Field(min_length=1, max_length=20)
    activo: bool = True


class PeriodoAcademicoIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=20)
    fecha_inicio: str
    fecha_fin: str
    activo: bool = False


class LimiteRolTipoIn(BaseModel):
    tipo_proyecto_id: str
    tipo_liderazgo: str = Field(min_length=1, max_length=20)
    rol_id: str
    cantidad_maxima: Optional[int] = Field(default=None, ge=0)
