from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class EncodedFileIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: Optional[str] = Field(default=None, max_length=100)
    data_base64: Optional[str] = None
    workflow_file_id: Optional[str] = None
    label: str = Field(min_length=1, max_length=120)
    person_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_source(self):
        if not self.workflow_file_id and not str(self.data_base64 or "").strip():
            raise ValueError("El archivo debe contener datos o referenciar una firma ya guardada.")
        if self.data_base64 and len(self.data_base64) < 8:
            raise ValueError("El contenido del archivo no es válido.")
        return self


class LifecycleMemberIn(BaseModel):
    persona_id: str
    role: Literal["corresponsable", "colaborador"] = "colaborador"
    horas_semanales: Optional[int] = Field(default=None, ge=2, le=5)


class LifecycleScheduleItem(BaseModel):
    semana: int = Field(ge=1, le=16)
    actividad: str = Field(min_length=1, max_length=2000)


class LifecycleBudgetItem(BaseModel):
    bien_servicio: str = Field(min_length=1, max_length=255)
    unidad_medida: Optional[str] = Field(default=None, max_length=50)
    cantidad: int = Field(ge=1)
    costo_unitario: float = Field(ge=0)
    partida: Literal["equipos_bienes", "insumos_materiales", "gastos_varios"] = "insumos_materiales"


class Annex12In(BaseModel):
    convocatoria_id: str
    linea_accion_id: str
    titulo: str = Field(min_length=3, max_length=255)
    domicilio_solicitante: str = Field(min_length=3, max_length=255)
    condicion_solicitante: str = Field(min_length=2, max_length=100)
    lugar_ejecucion: str = Field(min_length=2, max_length=255)
    fecha_inicio: Optional[date] = None
    fecha_fin: Optional[date] = None
    resumen: str = Field(min_length=10)
    problematica: str = Field(min_length=10)
    objetivos: str = Field(min_length=5)
    metodologia: str = Field(min_length=10)
    impacto_social: str = Field(min_length=10)
    impacto_estudiantes: str = Field(min_length=10)
    poblacion_objetivo: str = Field(min_length=5)
    miembros: List[LifecycleMemberIn] = Field(default_factory=list)
    cronograma: List[LifecycleScheduleItem] = Field(min_length=1)
    financiado: bool = False
    presupuesto: List[LifecycleBudgetItem] = Field(default_factory=list)
    firmas: List[EncodedFileIn] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_payload(self):
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin < self.fecha_inicio:
            raise ValueError("La fecha final no puede ser anterior a la fecha de inicio.")
        person_ids = [item.persona_id for item in self.miembros]
        if len(person_ids) != len(set(person_ids)):
            raise ValueError("No se puede registrar a una persona más de una vez en el equipo.")
        if sum(1 for item in self.miembros if item.role == "corresponsable") > 2:
            raise ValueError("Solo se permiten hasta dos corresponsables.")
        if sum(1 for item in self.miembros if item.role == "colaborador") > 7:
            raise ValueError("Un proyecto de RSU admite como máximo siete colaboradores cuando es liderado por un estudiante.")
        if self.financiado and not self.presupuesto:
            raise ValueError("Registra el presupuesto del proyecto financiado.")
        weeks = [item.semana for item in self.cronograma]
        if len(weeks) != len(set(weeks)):
            raise ValueError("No se puede repetir una semana del cronograma.")
        return self


class Annex3MemberIn(BaseModel):
    persona_id: str
    role: Literal["responsable", "corresponsable", "colaborador"]


class Annex3In(BaseModel):
    titulo: str = Field(min_length=3, max_length=255)
    linea_accion_id: str
    miembros: List[Annex3MemberIn] = Field(min_length=1)
    observaciones_registro: Optional[str] = None
    firma_responsable: Optional[EncodedFileIn] = None
    firmas: List[EncodedFileIn] = Field(default_factory=list)  # Firmas adicionales del Anexo 3

    @model_validator(mode="after")
    def validate_team(self):
        responsables = [x for x in self.miembros if x.role == "responsable"]
        if len(responsables) != 1:
            raise ValueError("El Anexo 3 debe contener exactamente un responsable.")
        if len({x.persona_id for x in self.miembros}) != len(self.miembros):
            raise ValueError("El equipo contiene personas duplicadas.")
        return self


class AnnexEvaluationIn(BaseModel):
    criterios: Dict[str, bool]
    observaciones_por_criterio: Dict[str, str] = Field(default_factory=dict)
    observaciones: Optional[str] = None
    firmas: List[EncodedFileIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_observations(self):
        missing_details = [
            key for key, approved in self.criterios.items()
            if not approved and not str(self.observaciones_por_criterio.get(key) or "").strip()
        ]
        if missing_details:
            raise ValueError(
                "Registra una observación específica para cada criterio marcado como NO: "
                + ", ".join(missing_details)
            )
        has_observations = bool(
            (self.observaciones or "").strip()
            or any(str(value or "").strip() for value in self.observaciones_por_criterio.values())
        )
        if not all(self.criterios.values()) and not has_observations:
            raise ValueError("Registra las observaciones de los criterios marcados como NO.")
        if all(self.criterios.values()) and has_observations:
            raise ValueError(
                "No se puede aprobar un anexo que contiene observaciones. "
                "Marca como NO el criterio observado y remítelo para corrección."
            )
        return self


class ResolutionActIn(BaseModel):
    numero_acta: str = Field(min_length=2, max_length=80)
    fecha_acta: date
    vicepresidente_nombre: str = Field(min_length=3, max_length=180)
    observaciones: Optional[str] = None
    archivo: EncodedFileIn


class IncidentIn(BaseModel):
    tipo: Literal["imprevisto", "retiro_miembro", "cambio_miembro", "otro"]
    descripcion: str = Field(min_length=10)
    miembro_afectado_id: Optional[str] = None
    reemplazo_propuesto_id: Optional[str] = None


class IncidentReviewIn(BaseModel):
    resultado: Literal["aprobado", "rechazado"]
    respuesta_admin: str = Field(min_length=3)


class Annex5In(BaseModel):
    duracion_horas: int = Field(ge=1)
    actividades_semanales: Dict[str, str]
    resumen_labores: str = Field(min_length=10)
    dificultades: str = Field(min_length=3)
    oportunidades_logros: str = Field(min_length=3)
    evidencias: List[EncodedFileIn] = Field(default_factory=list)
    firmas: List[EncodedFileIn] = Field(min_length=1)


class Annex6In(BaseModel):
    facultad: str = Field(min_length=2, max_length=150)
    escuela_profesional: str = Field(min_length=2, max_length=180)
    duracion_horas: int = Field(ge=1)
    actividades_semanales: Dict[str, str]
    resumen_labores: str = Field(min_length=10)
    dificultades: str = Field(min_length=3)
    resultados_logros: str = Field(min_length=3)
    recomendaciones: str = Field(min_length=3)
    evidencias: List[EncodedFileIn] = Field(default_factory=list)
    firmas: List[EncodedFileIn] = Field(min_length=1)


class CertificateIssueIn(BaseModel):
    horas_certificadas: int = Field(ge=32)
    fecha_emision: Optional[date] = None
    firma_director: Optional[EncodedFileIn] = None


class StartExecutionIn(BaseModel):
    fecha_inicio: Optional[date] = None


class WorkflowNoteIn(BaseModel):
    comentario: Optional[str] = Field(default=None, max_length=500)
