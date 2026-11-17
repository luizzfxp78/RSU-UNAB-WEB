from datetime import date
from typing import List, Optional
from pydantic import BaseModel, Field


class ParticipanteIn(BaseModel):
    persona_id: str
    rol_id: str
    horas_semanales: Optional[int] = Field(default=None, ge=0, le=40)
    fecha_asignacion: Optional[date] = None


class FinanciamientoIn(BaseModel):
    partida: str = Field(min_length=1, max_length=50)
    descripcion: str = Field(min_length=1)
    unidad_medida: Optional[str] = Field(default=None, max_length=30)
    cantidad: Optional[int] = Field(default=None, ge=0)
    costo_unitario: Optional[float] = Field(default=None, ge=0)
    monto_total: float = Field(ge=0)
    aprobado: bool = False


class ProyectoCreate(BaseModel):
    convocatoria_id: str
    periodo_academico_id: str
    linea_accion_id: str
    titulo: str = Field(min_length=1, max_length=255)
    tipo_liderazgo: str = Field(default="docente", max_length=20)
    financiado: bool = False
    estado_id: Optional[str] = None
    lugar_ejecucion: Optional[str] = Field(default=None, max_length=255)
    poblacion_objetivo: Optional[str] = None
    resumen: Optional[str] = None
    fecha_inicio: Optional[date] = None
    fecha_fin: Optional[date] = None
    participantes: List[ParticipanteIn] = []
    financiamiento: List[FinanciamientoIn] = []


class ProyectoEstadoPatch(BaseModel):
    estado_id: str
    observacion: Optional[str] = None


class EtapaCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=50)
    estado_id: str
    semana_limite: Optional[int] = None
    fecha_limite: Optional[date] = None
    observaciones: Optional[str] = None


class JornadaVoluntariadoCreate(BaseModel):
    rol_proyecto_id: str
    fecha: date
    horas: float = Field(ge=3)
    descripcion: Optional[str] = None
    evidencia_url: Optional[str] = Field(default=None, max_length=255)
