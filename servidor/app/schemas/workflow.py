from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class ConvocatoriaIn(BaseModel):
    tipo_proyecto_id: str
    periodo_academico_id: str
    fecha_inicio: date
    fecha_limite_presentacion: date
    monto_maximo_financiamiento: Optional[float] = Field(default=None, ge=0)
    bases_url: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.fecha_limite_presentacion < self.fecha_inicio:
            raise ValueError("La fecha límite no puede ser anterior a la fecha de inicio.")
        return self


class CronogramaItem(BaseModel):
    semana: str = Field(min_length=1, max_length=30)
    actividad: str = Field(min_length=1, max_length=1000)


class PresupuestoItem(BaseModel):
    bien_servicio: str = Field(min_length=1, max_length=255)
    unidad_medida: Optional[str] = Field(default=None, max_length=30)
    cantidad: int = Field(ge=1)
    costo_unitario: float = Field(ge=0)


class ProyectoFormularioIn(BaseModel):
    convocatoria_id: str
    linea_accion_id: str
    titulo: str = Field(min_length=3, max_length=255)
    solicitud_tipo: str = Field(default="Proyecto de RSU", max_length=120)
    domicilio_solicitante: Optional[str] = Field(default=None, max_length=255)
    condicion_solicitante: Optional[str] = Field(default=None, max_length=100)
    lugar_ejecucion: str = Field(min_length=2, max_length=255)
    poblacion_objetivo: str = Field(min_length=2)
    resumen: str = Field(min_length=10)
    problematica: str = Field(min_length=10)
    objetivo: str = Field(min_length=5)
    metodologia: str = Field(min_length=10)
    impacto_social: str = Field(min_length=10)
    impacto_estudiantes: str = Field(min_length=10)
    fecha_inicio: Optional[date] = None
    fecha_fin: Optional[date] = None
    colaboradores: List[str] = Field(default_factory=list, max_length=5)
    cronograma: List[CronogramaItem] = Field(min_length=1)
    financiado: bool = False
    presupuesto: List[PresupuestoItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_project(self):
        if len(self.colaboradores) > 5:
            raise ValueError("Solo se permiten hasta cinco colaboradores.")
        if len(set(self.colaboradores)) != len(self.colaboradores):
            raise ValueError("No se permiten colaboradores duplicados.")
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin < self.fecha_inicio:
            raise ValueError("La fecha final no puede ser anterior a la fecha de inicio.")
        if self.financiado and not self.presupuesto:
            raise ValueError("Agrega al menos una partida de presupuesto para un proyecto financiado.")
        return self


class EvaluacionProyectoIn(BaseModel):
    resultado: str = Field(pattern="^(aprobado|con_observaciones)$")
    observaciones: Optional[str] = None

    @model_validator(mode="after")
    def validate_observation(self):
        if self.resultado == "con_observaciones" and not (self.observaciones or "").strip():
            raise ValueError("Debes registrar las observaciones que el responsable corregirá.")
        return self
