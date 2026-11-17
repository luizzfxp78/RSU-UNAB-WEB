from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user, require_admin, deny_certificate_for_simulation
from app.core.config import get_settings
from app.db.mysql import execute, fetch_all, fetch_one, get_connection
from app.db.volunteer_schema import ensure_annual_volunteer_call
from app.schemas.auth import AuthUser
from app.schemas.project_lifecycle import EncodedFileIn
from app.services.certificate_pdf import generate_certificate_pdfs_batch
from app.services.download_filenames import certificate_download_filename
from app.services.notification_service import notify_admins, notify_project_responsibles, notify_users, project_user_ids
from app.services.volunteer_documents import (
    generate_annex1_pdf,
    generate_annex11_pdf,
    generate_annex12_pdf,
    generate_annex13_pdf,
    generate_volunteer_memorandum_pdf,
    merge_annex12_pdfs,
)


router = APIRouter(prefix="/voluntariados", tags=["Voluntariado RSU"])
_MEMO_LOCK = threading.RLock()
_INITIAL_SUBMISSION_LOCK = threading.RLock()


class VolunteerMemberIn(BaseModel):
    persona_id: str


class VolunteerCreateIn(BaseModel):
    titulo: str = Field(min_length=3, max_length=255)
    linea_accion_id: str
    fecha_inicio: date
    fecha_fin: date
    domicilio: str | None = None
    condicion: str | None = None
    beneficiario_institucion: str = Field(min_length=2)
    beneficiario_facultad: str | None = None
    beneficiario_escuela: str | None = None
    problemas: str = Field(min_length=5)
    necesidades: str = Field(min_length=5)
    objetivo: str = Field(min_length=5)
    actividades: str = Field(min_length=10)
    colaboradores: list[VolunteerMemberIn] = Field(min_length=1)
    firma_responsable: EncodedFileIn | None = None
    firmas_colaboradores: list[EncodedFileIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates_and_members(self):
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("La fecha de término no puede ser anterior a la fecha de inicio.")
        ids = [item.persona_id for item in self.colaboradores]
        if len(ids) != len(set(ids)):
            raise ValueError("No se permiten colaboradores duplicados.")
        signers = [str(item.person_id or '') for item in self.firmas_colaboradores]
        if len(signers) != len(set(signers)) or any(signer not in ids for signer in signers):
            raise ValueError("Cada firma de colaborador debe corresponder a un integrante distinto del voluntariado.")
        return self


class VolunteerRegistrationIn(BaseModel):
    persona_id: str
    tutor: dict[str, Any] = Field(default_factory=dict)
    voluntario: dict[str, Any]
    seguro: dict[str, Any] = Field(default_factory=dict)
    discapacidad: dict[str, Any] = Field(default_factory=dict)
    foto: EncodedFileIn
    firma: EncodedFileIn
    huella: EncodedFileIn


class VolunteerEvaluationIn(BaseModel):
    resultado: Literal["aprobado", "observado"]
    observaciones: str | None = None

    @model_validator(mode="after")
    def validate_observation(self):
        if self.resultado == "observado" and not str(self.observaciones or "").strip():
            raise ValueError("Registra la observación que deberá subsanar el responsable.")
        return self


class VolunteerStartIn(BaseModel):
    modalidad: Literal["interno", "externo"]
    fecha_inicio_real: date | None = None


class VolunteerWeekIn(BaseModel):
    semana: int = Field(ge=1, le=8)
    fecha: date | None = None
    hora: str | None = None
    descripcion: str = ""


class VolunteerClosureIn(BaseModel):
    fecha_inicio: date
    fecha_fin: date
    beneficiario_institucion: str = ""
    beneficiario_facultad: str | None = None
    beneficiario_escuela: str | None = None
    semanas: list[VolunteerWeekIn] = Field(default_factory=list)
    evidencias_descripcion: str = Field(min_length=3)
    evidencias: list[EncodedFileIn] = Field(default_factory=list)
    firma_responsable: EncodedFileIn | None = None

    @model_validator(mode="after")
    def validate_closure(self):
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("La fecha de término no puede ser anterior a la fecha de inicio.")
        if not any(str(item.descripcion or "").strip() for item in self.semanas):
            raise ValueError("Registra al menos una actividad semanal.")
        return self


class VolunteerCertificateIn(BaseModel):
    fecha_emision: date | None = None
    horas_certificadas: int = Field(default=16, ge=16, le=5000)


def _root() -> Path:
    root = Path(get_settings().private_storage_dir) / "voluntariados"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state_id(name: str) -> str:
    row = fetch_one(
        "SELECT catalogo_estado_id FROM catalogo_estado WHERE entidad='proyecto' AND LOWER(nombre)=LOWER(%s) LIMIT 1",
        (name,),
    )
    if not row:
        raise HTTPException(status_code=500, detail=f"No existe el estado interno {name}.")
    return str(row["catalogo_estado_id"])


def _role_id(name: str) -> str:
    row = fetch_one("SELECT rol_id FROM catalogo_rol WHERE LOWER(nombre)=LOWER(%s) LIMIT 1", (name,))
    if not row:
        raise HTTPException(status_code=500, detail=f"No existe el rol {name}.")
    return str(row["rol_id"])


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(str(value or "{}"))
    except Exception:
        return {}


def _person(person_id: str) -> dict[str, Any]:
    row = fetch_one(
        """
        SELECT p.persona_id,p.nombres,p.apellidos,CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo,p.dni,
               COALESCE(c.telefono,'') AS telefono,COALESCE(c.email_institucional,c.email_personal,'') AS email,
               COALESCE(c.direccion,'') AS domicilio,
               COALESCE(vp.pasaporte,'') AS pasaporte,
               COALESCE(vp.carne_extranjeria,'') AS carne_extranjeria,
               COALESCE(vp.distrito,'') AS distrito,
               COALESCE(vp.provincia,'') AS provincia,
               COALESCE(vp.departamento,'') AS departamento,
               COALESCE(e.codigo_estudiante,'') AS codigo_estudiante,
               COALESCE(dd.facultad,de.facultad,da.facultad,a.area,ap.facultad,'') AS facultad,
               COALESCE(dd.carrera,de.carrera,da.carrera,'') AS escuela,
               CASE
                   WHEN d.persona_id IS NOT NULL THEN 'Docente'
                   WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                   WHEN af.tipo_vinculo='no_docente' THEN 'No docente'
                   WHEN af.tipo_vinculo='egresado' THEN 'Egresado'
                   WHEN af.tipo_vinculo='graduado' THEN 'Graduado'
                   WHEN a.persona_id IS NOT NULL THEN 'Administrativo'
                   ELSE 'Persona'
               END AS tipo_persona,
               CASE
                   WHEN d.persona_id IS NOT NULL THEN COALESCE(NULLIF(TRIM(d.condicion),''),'Docente')
                   WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                   WHEN a.persona_id IS NOT NULL THEN COALESCE(NULLIF(TRIM(a.cargo),''),'Administrativo')
                   WHEN af.tipo_vinculo='no_docente' THEN 'No docente'
                   WHEN af.tipo_vinculo='egresado' THEN 'Egresado'
                   WHEN af.tipo_vinculo='graduado' THEN 'Graduado'
                   ELSE 'Persona'
               END AS condicion,
               CASE WHEN u.usuario_id IS NOT NULL THEN 1 ELSE 0 END AS tiene_cuenta,
               CASE WHEN u.usuario_id IS NOT NULL AND u.activo=1 THEN 1 ELSE 0 END AS cuenta_activa
        FROM persona p
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN rsu_person_volunteer_profile vp ON vp.persona_id=p.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN departamento_academico dd ON dd.departamento_academico_id=d.departamento_academico_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN departamento_academico de ON de.departamento_academico_id=e.departamento_academico_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        LEFT JOIN departamento_academico da ON da.departamento_academico_id=af.departamento_academico_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
        LEFT JOIN usuario u ON u.persona_id=p.persona_id
        WHERE p.persona_id=%s AND p.activo=1 LIMIT 1
        """,
        (person_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="La persona seleccionada no existe o está inactiva.")
    return row


def _collaborator(person_id: str) -> dict[str, Any]:
    """Valida a un colaborador.

    Las personas sin cuenta de acceso sí pueden participar. En ese caso, el
    responsable del voluntariado completa el Anexo 12 en su representación.
    Una cuenta existente pero inactiva continúa bloqueada hasta ser habilitada.
    """
    person = _person(person_id)
    if bool(person.get("tiene_cuenta")) and not bool(person.get("cuenta_activa")):
        raise HTTPException(
            status_code=400,
            detail=f"La cuenta de {person.get('nombre_completo') or 'la persona seleccionada'} está inactiva. Actívala o retira la cuenta para registrarla como participante sin acceso.",
        )
    return person


def _annex1_personal_data(person: dict[str, Any], data: VolunteerCreateIn, user: AuthUser) -> tuple[dict[str, str], str, bool]:
    stored_address = str(person.get("domicilio") or "").strip()
    entered_address = str(data.domicilio or "").strip()
    address = stored_address or entered_address
    if not address:
        raise HTTPException(
            status_code=400,
            detail="No existe un domicilio en los datos personales. Ingresa el domicilio para continuar.",
        )
    condition = str(person.get("condicion") or user.condicion or user.tipo_usuario or "Persona").strip()
    return {"domicilio": address, "condicion": condition}, address, not bool(stored_address)


def _store_missing_personal_address(cursor, person_id: str, address: str, should_store: bool) -> None:
    if not should_store or not str(address or "").strip():
        return
    cursor.execute("UPDATE contacto SET direccion=%s WHERE persona_id=%s", (address, person_id))
    if cursor.rowcount == 0:
        cursor.execute(
            "INSERT INTO contacto (contacto_id,persona_id,direccion) VALUES (%s,%s,%s)",
            (str(uuid4()), person_id, address),
        )


def _resolved_volunteer_personal_data(person: dict[str, Any], submitted: dict[str, Any]) -> dict[str, str]:
    """Protege los datos ya registrados y usa el formulario solo para completar faltantes."""
    mapping = {
        "dni": "dni",
        "pasaporte": "pasaporte",
        "carne_extranjeria": "carne_extranjeria",
        "apellidos": "apellidos",
        "nombres": "nombres",
        "direccion": "domicilio",
        "distrito": "distrito",
        "provincia": "provincia",
        "departamento": "departamento",
        "telefono": "telefono",
        "email": "email",
    }
    resolved: dict[str, str] = {}
    for output_key, database_key in mapping.items():
        stored = str(person.get(database_key) or "").strip()
        entered = str(submitted.get(output_key) or "").strip()
        resolved[output_key] = stored or entered
    return resolved


def _store_missing_volunteer_personal_data(
    cursor,
    person_id: str,
    original: dict[str, Any],
    resolved: dict[str, str],
) -> None:
    """Guarda únicamente campos que no existían en el perfil personal."""
    contact_fields = {
        "direccion": "domicilio",
        "telefono": "telefono",
        "email": "email",
    }
    contact_values: dict[str, str] = {}
    for submitted_key, original_key in contact_fields.items():
        if not str(original.get(original_key) or "").strip() and str(resolved.get(submitted_key) or "").strip():
            contact_values[submitted_key] = str(resolved[submitted_key]).strip()

    cursor.execute("SELECT contacto_id FROM contacto WHERE persona_id=%s LIMIT 1", (person_id,))
    contact = cursor.fetchone()
    if contact:
        if contact_values:
            assignments: list[str] = []
            params: list[Any] = []
            if "direccion" in contact_values:
                assignments.append("direccion=%s")
                params.append(contact_values["direccion"])
            if "telefono" in contact_values:
                assignments.append("telefono=%s")
                params.append(contact_values["telefono"])
            if "email" in contact_values:
                assignments.append("email_personal=%s")
                params.append(contact_values["email"])
            params.append(person_id)
            cursor.execute(f"UPDATE contacto SET {', '.join(assignments)} WHERE persona_id=%s", tuple(params))
    elif contact_values:
        cursor.execute(
            """INSERT INTO contacto
               (contacto_id,persona_id,email_personal,telefono,direccion)
               VALUES (%s,%s,%s,%s,%s)""",
            (
                str(uuid4()),
                person_id,
                contact_values.get("email") or None,
                contact_values.get("telefono") or None,
                contact_values.get("direccion") or None,
            ),
        )

    profile_fields = ("pasaporte", "carne_extranjeria", "distrito", "provincia", "departamento")
    profile_values = {
        key: str(resolved.get(key) or "").strip()
        for key in profile_fields
        if not str(original.get(key) or "").strip() and str(resolved.get(key) or "").strip()
    }
    if profile_values:
        cursor.execute(
            """INSERT INTO rsu_person_volunteer_profile
               (persona_id,pasaporte,carne_extranjeria,distrito,provincia,departamento)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE
                 pasaporte=COALESCE(NULLIF(pasaporte,''),VALUES(pasaporte)),
                 carne_extranjeria=COALESCE(NULLIF(carne_extranjeria,''),VALUES(carne_extranjeria)),
                 distrito=COALESCE(NULLIF(distrito,''),VALUES(distrito)),
                 provincia=COALESCE(NULLIF(provincia,''),VALUES(provincia)),
                 departamento=COALESCE(NULLIF(departamento,''),VALUES(departamento))""",
            (
                person_id,
                profile_values.get("pasaporte") or None,
                profile_values.get("carne_extranjeria") or None,
                profile_values.get("distrito") or None,
                profile_values.get("provincia") or None,
                profile_values.get("departamento") or None,
            ),
        )


def _authority(role_code: str, faculty: str | None = None) -> dict[str, Any] | None:
    params: list[Any] = [role_code]
    faculty_sql = ""
    if faculty:
        faculty_sql = " AND LOWER(TRIM(COALESCE(usr.facultad,ap.facultad,'')))=LOWER(TRIM(%s))"
        params.append(faculty)
    return fetch_one(
        f"""
        SELECT u.usuario_id,p.persona_id,CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo,p.dni,
               COALESCE(c.email_institucional,c.email_personal,'') AS email,
               COALESCE(usr.facultad,ap.facultad,'') AS facultad,
               COALESCE(ap.cargo_oficial,a.cargo,sr.nombre) AS cargo,ap.firma_url
        FROM rsu_user_system_role usr
        INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id
        INNER JOIN usuario u ON u.usuario_id=usr.usuario_id AND u.activo=1
        INNER JOIN persona p ON p.persona_id=u.persona_id AND p.activo=1
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
        WHERE sr.codigo=%s AND usr.activo=1 AND sr.activo=1 {faculty_sql}
        ORDER BY usr.fecha_asignacion DESC LIMIT 1
        """,
        tuple(params),
    )


def _project_row(project_id: str) -> dict[str, Any]:
    row = fetch_one(
        """
        SELECT p.*,CONCAT(la.codigo,'-',la.nombre) AS linea_accion,la.codigo AS linea_accion_codigo,pa.nombre AS periodo_academico,tp.nombre AS tipo_proyecto,
               w.estado_flujo,w.etapa_actual,w.modalidad,w.revision_inicial,w.revision_final,
               w.fecha_limite_subsanacion,w.fecha_inicio_ejecucion,w.fecha_certificacion
        FROM proyecto p
        INNER JOIN rsu_volunteer_workflow w ON w.proyecto_id=p.proyecto_id
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=p.periodo_academico_id
        INNER JOIN convocatoria c ON c.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=c.tipo_proyecto_id
        WHERE p.proyecto_id=%s LIMIT 1
        """,
        (project_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="El voluntariado no existe.")
    return row


def _responsible(project_id: str) -> dict[str, Any]:
    row = fetch_one(
        """
        SELECT p.persona_id,CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo,p.dni,
               COALESCE(c.telefono,'') AS telefono,COALESCE(c.email_institucional,c.email_personal,'') AS email,
               COALESCE(c.direccion,'') AS domicilio,
               COALESCE(dd.facultad,de.facultad,da.facultad,a.area,ap.facultad,'') AS facultad,
               COALESCE(dd.carrera,de.carrera,da.carrera,'') AS escuela,
               CASE
                   WHEN d.persona_id IS NOT NULL THEN COALESCE(NULLIF(TRIM(d.condicion),''),'Docente')
                   WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                   WHEN a.persona_id IS NOT NULL THEN COALESCE(NULLIF(TRIM(a.cargo),''),'Administrativo')
                   WHEN af.tipo_vinculo='no_docente' THEN 'No docente'
                   WHEN af.tipo_vinculo='egresado' THEN 'Egresado'
                   WHEN af.tipo_vinculo='graduado' THEN 'Graduado'
                   ELSE 'Persona'
               END AS condicion
        FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id AND LOWER(cr.nombre)='responsable'
        INNER JOIN persona p ON p.persona_id=rp.persona_id
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id LEFT JOIN departamento_academico dd ON dd.departamento_academico_id=d.departamento_academico_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id LEFT JOIN departamento_academico de ON de.departamento_academico_id=e.departamento_academico_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id LEFT JOIN departamento_academico da ON da.departamento_academico_id=af.departamento_academico_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
        WHERE rp.proyecto_id=%s AND rp.activo=1 LIMIT 1
        """,
        (project_id,),
    )
    if not row:
        raise HTTPException(status_code=500, detail="El voluntariado no tiene responsable.")
    return row


def _members(project_id: str) -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT p.persona_id,CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo,p.nombres,p.apellidos,p.dni,
               COALESCE(e.codigo_estudiante,'') AS codigo_estudiante,COALESCE(c.telefono,'') AS telefono,
               COALESCE(c.email_institucional,c.email_personal,'') AS email,
               COALESCE(c.direccion,'') AS domicilio,
               COALESCE(vp.pasaporte,'') AS pasaporte,
               COALESCE(vp.carne_extranjeria,'') AS carne_extranjeria,
               COALESCE(vp.distrito,'') AS distrito,
               COALESCE(vp.provincia,'') AS provincia,
               COALESCE(vp.departamento,'') AS departamento,
               CASE WHEN EXISTS (SELECT 1 FROM docente dx WHERE dx.persona_id=p.persona_id) THEN 'Docente'
                    WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                    WHEN EXISTS (SELECT 1 FROM administrativo ax WHERE ax.persona_id=p.persona_id) THEN 'Administrativo'
                    WHEN EXISTS (SELECT 1 FROM rsu_person_affiliation fx WHERE fx.persona_id=p.persona_id AND fx.tipo_vinculo='no_docente') THEN 'No docente'
                    WHEN EXISTS (SELECT 1 FROM rsu_person_affiliation fx WHERE fx.persona_id=p.persona_id AND fx.tipo_vinculo='egresado') THEN 'Egresado'
                    WHEN EXISTS (SELECT 1 FROM rsu_person_affiliation fx WHERE fx.persona_id=p.persona_id AND fx.tipo_vinculo='graduado') THEN 'Graduado'
                    ELSE 'Persona' END AS tipo_persona,
               CASE WHEN EXISTS (SELECT 1 FROM usuario ux WHERE ux.persona_id=p.persona_id) THEN 1 ELSE 0 END AS tiene_cuenta,
               CASE WHEN EXISTS (SELECT 1 FROM usuario ux WHERE ux.persona_id=p.persona_id AND ux.activo=1) THEN 1 ELSE 0 END AS cuenta_activa,
               CASE WHEN EXISTS (SELECT 1 FROM usuario ux WHERE ux.persona_id=p.persona_id AND ux.activo=1) THEN 'Cuenta activa'
                    WHEN EXISTS (SELECT 1 FROM usuario ux WHERE ux.persona_id=p.persona_id) THEN 'Cuenta inactiva'
                    ELSE 'Sin cuenta de acceso' END AS acceso,
               CASE WHEN EXISTS (SELECT 1 FROM usuario ux WHERE ux.persona_id=p.persona_id AND ux.activo=1)
                         OR NOT EXISTS (SELECT 1 FROM usuario ux WHERE ux.persona_id=p.persona_id)
                    THEN 1 ELSE 0 END AS puede_agregarse,
               CASE WHEN EXISTS (SELECT 1 FROM usuario ux WHERE ux.persona_id=p.persona_id AND ux.activo=1)
                    THEN 'Colaborador'
                    WHEN NOT EXISTS (SELECT 1 FROM usuario ux WHERE ux.persona_id=p.persona_id)
                    THEN 'Responsable del voluntariado'
                    ELSE 'No disponible' END AS responsable_ficha,
               COALESCE(vr.estado,'pendiente') AS estado_anexo12,vr.revision AS revision_anexo12,
               vr.datos_voluntario_json
        FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id AND LOWER(cr.nombre)='voluntario'
        INNER JOIN persona p ON p.persona_id=rp.persona_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN rsu_person_volunteer_profile vp ON vp.persona_id=p.persona_id
        LEFT JOIN rsu_volunteer_registration vr ON vr.proyecto_id=rp.proyecto_id AND vr.persona_id=rp.persona_id AND vr.es_actual=1
        WHERE rp.proyecto_id=%s AND rp.activo=1
        ORDER BY rp.fecha_asignacion,p.apellidos,p.nombres
        """,
        (project_id,),
    )
    for row in rows:
        saved = _json(row.pop("datos_voluntario_json", None))
        fallbacks = {
            "pasaporte": "pasaporte",
            "carne_extranjeria": "carne_extranjeria",
            "domicilio": "direccion",
            "distrito": "distrito",
            "provincia": "provincia",
            "departamento": "departamento",
            "telefono": "telefono",
            "email": "email",
        }
        for target, source in fallbacks.items():
            if not str(row.get(target) or "").strip() and str(saved.get(source) or "").strip():
                row[target] = str(saved.get(source) or "").strip()
    return rows


def _signature_path(project_id: str, person_id: str, purpose: str = "firma_responsable") -> str | None:
    row = fetch_one(
        """SELECT archivo_url FROM rsu_workflow_file
           WHERE proyecto_id=%s AND persona_firmante_id=%s AND proposito=%s
           ORDER BY fecha_subida DESC LIMIT 1""",
        (project_id, person_id, purpose),
    )
    return str(row["archivo_url"]) if row else None


def _assert_access(project_id: str, user: AuthUser) -> dict[str, Any]:
    project = _project_row(project_id)
    role = str(user.rol_sistema or "").lower()
    if role in {"administrador_rsu", "director_rsu", "vicepresidente_academico"}:
        return project
    member = fetch_one(
        "SELECT 1 AS ok FROM rol_proyecto WHERE proyecto_id=%s AND persona_id=%s AND activo=1 LIMIT 1",
        (project_id, user.persona_id),
    )
    if member:
        return project
    if role == "director_facultad":
        responsible = _responsible(project_id)
        if str(responsible.get("facultad") or "").strip().lower() == str(user.facultad_asignada or "").strip().lower():
            return project
    raise HTTPException(status_code=403, detail="No tienes acceso a este expediente de voluntariado.")


def _enforce_correction_deadline(project: dict[str, Any]) -> None:
    """No admitir subsanaciones de voluntariado fuera de los tres días hábiles asignados.

    La evaluación almacena la fecha usando calendario_laboral. El cambio de estado
    o la eventual excepcionalidad corresponde a RSU, nunca al cliente web.
    """
    state = str(project.get("estado_flujo") or "")
    if state not in {"voluntariado_observado_inicial", "voluntariado_observado_final", "voluntariado_pendiente_fichas"}:
        return
    due = project.get("fecha_limite_subsanacion")
    # La primera creación permanece pendiente de fichas y no está observada.
    if state == "voluntariado_pendiente_fichas" and not due:
        return
    if not due:
        raise HTTPException(status_code=409, detail="El plazo de subsanación no está definido. Comunícate con RSU.")
    deadline = due if isinstance(due, date) else date.fromisoformat(str(due)[:10])
    if date.today() > deadline:
        raise HTTPException(
            status_code=409,
            detail=f"Venció el plazo de tres días hábiles para subsanar el {deadline:%d/%m/%Y}. Solicita indicaciones a la Dirección de RSU.",
        )


def _assert_owner(project_id: str, user: AuthUser) -> dict[str, Any]:
    project = _assert_access(project_id, user)
    _enforce_correction_deadline(project)
    responsible = _responsible(project_id)
    if str(responsible["persona_id"]) != str(user.persona_id):
        raise HTTPException(status_code=403, detail="Solo el responsable puede realizar esta acción.")
    return project


def _add_business_days(start: date, amount: int) -> date:
    current = start
    remaining = int(amount)
    while remaining > 0:
        current += timedelta(days=1)
        calendar = fetch_one("SELECT es_laborable FROM calendario_laboral WHERE fecha=%s", (current,))
        is_business = bool(calendar["es_laborable"]) if calendar else current.weekday() < 5
        if is_business:
            remaining -= 1
    return current


def _current_submission(project_id: str, annex: int) -> dict[str, Any] | None:
    row = fetch_one(
        """SELECT * FROM rsu_annex_submission WHERE proyecto_id=%s AND anexo_numero=%s AND es_actual=1
           ORDER BY revision DESC LIMIT 1""",
        (project_id, annex),
    )
    if row:
        row["contenido"] = _json(row.get("contenido_json"))
    return row


def _insert_submission(cur, project_id: str, annex: int, person_id: str, content: dict[str, Any], revision: int) -> str:
    submission_id = str(uuid4())
    cur.execute("UPDATE rsu_annex_submission SET es_actual=0 WHERE proyecto_id=%s AND anexo_numero=%s", (project_id, annex))
    cur.execute(
        """INSERT INTO rsu_annex_submission
           (submission_id,proyecto_id,anexo_numero,revision,presentado_por,contenido_json,estado,es_actual)
           VALUES (%s,%s,%s,%s,%s,%s,'presentado',1)""",
        (submission_id, project_id, annex, revision, person_id, json.dumps(content, ensure_ascii=False, default=str)),
    )
    return submission_id


def _save_encoded_file(project_id: str, item: EncodedFileIn, person_id: str, purpose: str, folder: str) -> str:
    try:
        content = base64.b64decode(str(item.data_base64 or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"El archivo {item.label or item.filename} no es válido.") from exc
    if not content or len(content) > 12 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="El archivo está vacío o supera 12 MB.")
    extension = Path(item.filename).suffix.lower()
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
    if extension not in allowed:
        raise HTTPException(status_code=400, detail="Solo se permiten imágenes o PDF.")
    path = _root() / project_id / folder / f"{uuid4().hex}{extension}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    execute(
        """INSERT INTO rsu_workflow_file
           (workflow_file_id,proyecto_id,anexo_numero,proposito,etiqueta,persona_firmante_id,
            archivo_url,nombre_archivo,mime_type,subido_por)
           VALUES (UUID(),%s,NULL,%s,%s,%s,%s,%s,%s,%s)""",
        (project_id, purpose, item.label or purpose, person_id, str(path), item.filename, item.mime_type or mimetypes.guess_type(item.filename)[0], person_id),
    )
    return str(path)


def _store_responsible_signature(project_id: str, item: EncodedFileIn | None, person_id: str) -> str | None:
    if not item:
        return _signature_path(project_id, person_id)
    return _save_encoded_file(project_id, item, person_id, "firma_responsable", "firmas")


def _assign_reviewers(cur, project_id: str, annex: int, revision: int, *, external: bool = False) -> None:
    responsible = _responsible(project_id)
    admin = _authority("administrador_rsu") or _authority("director_rsu")
    director = None if external else _authority("director_facultad", str(responsible.get("facultad") or ""))
    if not admin:
        raise HTTPException(status_code=400, detail="No existe un administrador RSU activo.")
    if not external and not director:
        raise HTTPException(status_code=400, detail="La facultad del responsable no tiene Director de Facultad activo.")
    cur.execute(
        """INSERT INTO rsu_project_review_assignment
           (assignment_id,proyecto_id,anexo_numero,revision,rol_revisor,revisor_persona_id,facultad,estado,activo)
           VALUES (%s,%s,%s,%s,'administrador_rsu',%s,NULL,'pendiente',1)""",
        (str(uuid4()), project_id, annex, revision, admin["persona_id"]),
    )
    if director:
        cur.execute(
            """INSERT INTO rsu_project_review_assignment
               (assignment_id,proyecto_id,anexo_numero,revision,rol_revisor,revisor_persona_id,facultad,estado,activo)
               VALUES (%s,%s,%s,%s,'director_facultad',%s,%s,'pendiente',1)""",
            (str(uuid4()), project_id, annex, revision, director["persona_id"], responsible.get("facultad")),
        )


def _assignment(project_id: str, annex: int, user: AuthUser) -> dict[str, Any] | None:
    role = str(user.rol_sistema or "").lower()
    return fetch_one(
        """SELECT * FROM rsu_project_review_assignment
           WHERE proyecto_id=%s AND anexo_numero=%s AND activo=1 AND estado='pendiente'
             AND (revisor_persona_id=%s OR (rol_revisor='administrador_rsu' AND %s IN ('administrador_rsu','director_rsu')))
           ORDER BY fecha_asignacion DESC LIMIT 1""",
        (project_id, annex, user.persona_id, role),
    )


def _reviews(project_id: str, annex: int) -> list[dict[str, Any]]:
    return fetch_all(
        """SELECT a.assignment_id,a.rol_revisor,a.estado,a.revision,a.fecha_revision,a.facultad,
                  CONCAT(p.nombres,' ',p.apellidos) AS revisor,e.observaciones,e.fecha_limite_subsanacion
           FROM rsu_project_review_assignment a
           LEFT JOIN persona p ON p.persona_id=a.revisor_persona_id
           LEFT JOIN rsu_annex_evaluation e ON e.evaluation_id=a.evaluation_id
           WHERE a.proyecto_id=%s AND a.anexo_numero=%s
           ORDER BY a.revision DESC,a.rol_revisor""",
        (project_id, annex),
    )


def _can_write_registration(project_id: str, person_id: str, user: AuthUser) -> None:
    responsible = _responsible(project_id)
    if str(user.persona_id) not in {str(person_id), str(responsible["persona_id"])}:
        raise HTTPException(status_code=403, detail="Solo el colaborador o el responsable puede completar esta ficha.")
    member = fetch_one(
        """SELECT 1 AS ok FROM rol_proyecto rp INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
           WHERE rp.proyecto_id=%s AND rp.persona_id=%s AND LOWER(cr.nombre)='voluntario' AND rp.activo=1 LIMIT 1""",
        (project_id, person_id),
    )
    if not member:
        raise HTTPException(status_code=400, detail="La persona no está registrada como colaborador del voluntariado.")


def _regenerate_bundle(project_id: str) -> dict[str, Any] | None:
    registrations = fetch_all(
        """SELECT vr.*,CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo
           FROM rsu_volunteer_registration vr INNER JOIN persona p ON p.persona_id=vr.persona_id
           INNER JOIN rol_proyecto rp ON rp.proyecto_id=vr.proyecto_id AND rp.persona_id=vr.persona_id AND rp.activo=1
           WHERE vr.proyecto_id=%s AND vr.es_actual=1 AND vr.estado='completado'
           ORDER BY rp.fecha_asignacion,p.apellidos,p.nombres""",
        (project_id,),
    )
    if not registrations:
        return None
    bundle_row = fetch_one("SELECT COALESCE(MAX(revision),0)+1 AS revision FROM rsu_volunteer_annex12_bundle WHERE proyecto_id=%s", (project_id,))
    revision = int((bundle_row or {}).get("revision") or 1)
    output = _root() / project_id / "anexo12" / f"anexo_12_consolidado_r{revision}.pdf"
    pdfs = [Path(str(item["pdf_url"])) for item in registrations if item.get("pdf_url") and Path(str(item["pdf_url"])).exists()]
    merged, pages, digest = merge_annex12_pdfs(output, pdfs)
    manifest = {
        "revision": revision,
        "participantes": [
            {"persona_id": item["persona_id"], "nombre": item["nombre_completo"], "revision_ficha": item["revision"]}
            for item in registrations
        ],
        "cantidad_paginas": pages,
    }
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("UPDATE rsu_volunteer_annex12_bundle SET es_actual=0 WHERE proyecto_id=%s", (project_id,))
            cur.execute(
                """INSERT INTO rsu_volunteer_annex12_bundle
                   (bundle_id,proyecto_id,revision,archivo_url,manifest_json,cantidad_fichas,cantidad_paginas,sha256,es_actual)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1)""",
                (str(uuid4()), project_id, revision, str(merged), json.dumps(manifest, ensure_ascii=False), len(registrations), pages, digest),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    return {"archivo_url": str(merged), "revision": revision, "cantidad_fichas": len(registrations), "cantidad_paginas": pages, "sha256": digest, "manifest": manifest}


def _issue_memorandum(project_id: str, kind: Literal["aprobacion_inicial", "aprobacion_final"], emitter: AuthUser) -> dict[str, Any]:
    with _MEMO_LOCK:
        existing = fetch_one("SELECT * FROM rsu_volunteer_memorandum WHERE proyecto_id=%s AND tipo=%s LIMIT 1", (project_id, kind))
        if existing:
            return existing
        project = _project_row(project_id)
        responsible = _responsible(project_id)
        director = _authority("director_rsu") or _authority("administrador_rsu")
        vice = _authority("vicepresidente_academico")
        count = fetch_one("SELECT COUNT(*) AS total FROM rsu_volunteer_memorandum WHERE YEAR(fecha_emision)=YEAR(CURDATE())") or {}
        number = f"VOL-{date.today().year}-{int(count.get('total') or 0)+1:04d}-UNAB"
        output = _root() / project_id / "memorandums" / f"{number}.pdf"
        generate_volunteer_memorandum_pdf(
            output, number=number, issue_date=date.today(), project=project, responsible=responsible,
            director=director, vice_president=vice, final=kind == "aprobacion_final",
        )
        memorandum_id = str(uuid4())
        execute(
            """INSERT INTO rsu_volunteer_memorandum
               (memorandum_id,proyecto_id,tipo,numero_memorandum,fecha_emision,archivo_pdf_url,
                responsable_persona_id,vicepresidente_persona_id,directora_persona_id,emitido_por)
               VALUES (%s,%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s)""",
            (memorandum_id, project_id, kind, number, str(output), responsible["persona_id"],
             (vice or {}).get("persona_id"), (director or {}).get("persona_id"), emitter.persona_id),
        )
        return fetch_one("SELECT * FROM rsu_volunteer_memorandum WHERE memorandum_id=%s", (memorandum_id,)) or {}


def _payload(project_id: str, user: AuthUser) -> dict[str, Any]:
    project = _assert_access(project_id, user)
    responsible = _responsible(project_id)
    members = _members(project_id)
    bundle = fetch_one("SELECT * FROM rsu_volunteer_annex12_bundle WHERE proyecto_id=%s AND es_actual=1 LIMIT 1", (project_id,))
    memos = fetch_all("SELECT * FROM rsu_volunteer_memorandum WHERE proyecto_id=%s ORDER BY fecha_registro", (project_id,))
    certificates = fetch_all(
        """SELECT c.certificado_id,c.persona_id,c.horas_certificadas,c.fecha_emision,c.archivo_url,
                  CONCAT(p.nombres,' ',p.apellidos) AS participante
           FROM certificado c INNER JOIN persona p ON p.persona_id=c.persona_id
           WHERE c.proyecto_id=%s ORDER BY p.apellidos,p.nombres""",
        (project_id,),
    )
    a1, a11, a13 = (_current_submission(project_id, n) for n in (1, 11, 13))
    can_initial = bool(_assignment(project_id, 12, user))
    can_final = bool(_assignment(project_id, 13, user))
    completed = sum(1 for item in members if item.get("estado_anexo12") == "completado")
    return {
        "proyecto": project,
        "responsable": responsible,
        "colaboradores": members,
        "firmas_previas": fetch_all(
            """SELECT workflow_file_id, persona_firmante_id, etiqueta FROM rsu_workflow_file
               WHERE proyecto_id=%s AND proposito='firma_anexo12_previa' ORDER BY fecha_subida DESC""",
            (project_id,),
        ),
        "anexo_1": a1,
        "anexo_11": a11,
        "anexo_13": a13,
        "anexo_12_consolidado": bundle,
        "fichas_completadas": completed,
        "fichas_total": len(members),
        "evaluaciones_iniciales": _reviews(project_id, 12),
        "evaluaciones_finales": _reviews(project_id, 13),
        "memorandums": memos,
        "certificados": certificates,
        "puede_evaluar_inicial": can_initial,
        "puede_evaluar_final": can_final,
        "es_responsable": str(responsible["persona_id"]) == str(user.persona_id),
        "es_colaborador": any(str(item["persona_id"]) == str(user.persona_id) for item in members),
    }


@router.get("/convocatoria-vigente")
def annual_call(user: AuthUser = Depends(get_current_user)):
    year = date.today().year
    call_id = ensure_annual_volunteer_call(year)
    row = fetch_one(
        """SELECT c.*,tp.nombre AS tipo_proyecto,pa.nombre AS periodo_academico,va.permanente,va.activa
           FROM convocatoria c INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=c.tipo_proyecto_id
           INNER JOIN periodo_academico pa ON pa.periodo_academico_id=c.periodo_academico_id
           INNER JOIN rsu_volunteer_annual_call va ON va.convocatoria_id=c.convocatoria_id
           WHERE c.convocatoria_id=%s""",
        (call_id,),
    )
    return row


@router.get("/catalogos-beneficiarios")
def volunteer_beneficiary_catalogs(_: AuthUser = Depends(get_current_user)):
    faculty_rows = fetch_all(
        """
        SELECT DISTINCT TRIM(facultad) AS facultad
        FROM departamento_academico
        WHERE activo=1 AND NULLIF(TRIM(facultad),'') IS NOT NULL
        ORDER BY facultad
        """
    )
    school_rows = fetch_all(
        """
        SELECT departamento_academico_id,codigo,TRIM(facultad) AS facultad,TRIM(carrera) AS escuela
        FROM departamento_academico
        WHERE activo=1
          AND NULLIF(TRIM(facultad),'') IS NOT NULL
          AND NULLIF(TRIM(carrera),'') IS NOT NULL
        ORDER BY facultad,escuela
        """
    )
    faculties = [str(item.get("facultad") or "").strip() for item in faculty_rows if str(item.get("facultad") or "").strip()]
    return {"facultades": faculties, "escuelas": school_rows}


@router.get("/personas-disponibles")
def volunteer_people(q: str | None = None, user: AuthUser = Depends(get_current_user)):
    params: list[Any] = []
    filter_sql = ""
    if q and q.strip():
        like = f"%{q.strip()}%"
        filter_sql = " AND (p.nombres LIKE %s OR p.apellidos LIKE %s OR p.dni LIKE %s OR CONCAT(p.nombres,' ',p.apellidos) LIKE %s)"
        params.extend([like, like, like, like])
    rows = fetch_all(
        f"""
        SELECT p.persona_id,p.nombres,p.apellidos,CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo,p.dni,
               COALESCE(c.telefono,'') AS telefono,COALESCE(c.email_institucional,c.email_personal,'') AS email,
               CASE WHEN d.persona_id IS NOT NULL THEN 'Docente'
                    WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                    WHEN af.tipo_vinculo='no_docente' THEN 'No docente'
                    WHEN af.tipo_vinculo='egresado' THEN 'Egresado'
                    WHEN af.tipo_vinculo='graduado' THEN 'Graduado'
                    WHEN a.persona_id IS NOT NULL THEN 'Administrativo'
                    ELSE 'Persona' END AS tipo_persona,
               CASE WHEN u.usuario_id IS NOT NULL THEN 1 ELSE 0 END AS tiene_cuenta,
               CASE WHEN u.usuario_id IS NOT NULL AND u.activo=1 THEN 1 ELSE 0 END AS cuenta_activa,
               CASE WHEN p.persona_id=%s THEN 1 ELSE 0 END AS es_responsable
        FROM persona p
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        LEFT JOIN usuario u ON u.persona_id=p.persona_id
        WHERE p.activo=1 {filter_sql}
        ORDER BY p.apellidos,p.nombres
        """,
        tuple([user.persona_id, *params]),
    )
    for item in rows:
        if bool(item.get("es_responsable")):
            item["acceso"] = "Responsable del voluntariado"
            item["puede_agregarse"] = False
        elif bool(item.get("cuenta_activa")):
            item["acceso"] = "Cuenta activa"
            item["puede_agregarse"] = True
            item["responsable_ficha"] = "Colaborador"
        elif bool(item.get("tiene_cuenta")):
            item["acceso"] = "Cuenta inactiva"
            item["puede_agregarse"] = False
            item["responsable_ficha"] = "No disponible"
        else:
            item["acceso"] = "Sin cuenta de acceso"
            item["puede_agregarse"] = True
            item["responsable_ficha"] = "Responsable del voluntariado"
    return rows


@router.get("")
def list_volunteers(user: AuthUser = Depends(get_current_user)):
    role = str(user.rol_sistema or "").lower()
    params: list[Any] = [user.persona_id, user.persona_id, user.persona_id, user.persona_id, user.persona_id]
    if role in {"administrador_rsu", "director_rsu", "vicepresidente_academico"}:
        scope = "1=1"
    elif role == "director_facultad":
        scope = "LOWER(TRIM(COALESCE(rf.facultad,'')))=LOWER(TRIM(%s))"
        params.append(user.facultad_asignada or "")
    else:
        scope = "EXISTS (SELECT 1 FROM rol_proyecto rm WHERE rm.proyecto_id=p.proyecto_id AND rm.persona_id=%s AND rm.activo=1)"
        params.append(user.persona_id)
    rows = fetch_all(
        f"""
        SELECT p.proyecto_id,p.titulo,p.fecha_inicio,p.fecha_fin,CONCAT(la.codigo,'-',la.nombre) AS linea_accion,pa.nombre AS periodo_academico,w.estado_flujo,w.etapa_actual,w.modalidad,
               w.fecha_limite_subsanacion,CONCAT(pr.nombres,' ',pr.apellidos) AS responsable,rf.facultad,
               CASE
                   WHEN rr.persona_id=%s THEN 'responsable'
                   WHEN EXISTS (
                       SELECT 1
                       FROM rol_proyecto rpm
                       INNER JOIN catalogo_rol crm ON crm.rol_id=rpm.rol_id AND LOWER(crm.nombre)='voluntario'
                       WHERE rpm.proyecto_id=p.proyecto_id AND rpm.persona_id=%s AND rpm.activo=1
                   ) THEN 'colaborador'
                   ELSE ''
               END AS mi_rol,
               COALESCE((
                   SELECT vru.estado
                   FROM rsu_volunteer_registration vru
                   WHERE vru.proyecto_id=p.proyecto_id AND vru.persona_id=%s AND vru.es_actual=1
                   ORDER BY vru.revision DESC LIMIT 1
               ),'pendiente') AS mi_estado_anexo12,
               EXISTS (
                   SELECT 1 FROM rsu_project_review_assignment rai
                   WHERE rai.proyecto_id=p.proyecto_id AND rai.revisor_persona_id=%s
                     AND rai.anexo_numero=12 AND rai.estado='pendiente' AND rai.activo=1
               ) AS puede_evaluar_inicial,
               EXISTS (
                   SELECT 1 FROM rsu_project_review_assignment raf
                   WHERE raf.proyecto_id=p.proyecto_id AND raf.revisor_persona_id=%s
                     AND raf.anexo_numero=13 AND raf.estado='pendiente' AND raf.activo=1
               ) AS puede_evaluar_final,
               COUNT(DISTINCT rv.persona_id) AS colaboradores,
               SUM(CASE WHEN vr.estado='completado' AND vr.es_actual=1 THEN 1 ELSE 0 END) AS fichas_completadas
        FROM proyecto p INNER JOIN rsu_volunteer_workflow w ON w.proyecto_id=p.proyecto_id
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=p.periodo_academico_id
        INNER JOIN rol_proyecto rr ON rr.proyecto_id=p.proyecto_id AND rr.activo=1
        INNER JOIN catalogo_rol cr ON cr.rol_id=rr.rol_id AND LOWER(cr.nombre)='responsable'
        INNER JOIN persona pr ON pr.persona_id=rr.persona_id
        LEFT JOIN (
          SELECT rp.proyecto_id,COALESCE(dd.facultad,de.facultad,da.facultad,a.area,ap.facultad,'') AS facultad
          FROM rol_proyecto rp INNER JOIN catalogo_rol r ON r.rol_id=rp.rol_id AND LOWER(r.nombre)='responsable'
          INNER JOIN persona pp ON pp.persona_id=rp.persona_id
          LEFT JOIN docente d ON d.persona_id=pp.persona_id LEFT JOIN departamento_academico dd ON dd.departamento_academico_id=d.departamento_academico_id
          LEFT JOIN estudiante e ON e.persona_id=pp.persona_id LEFT JOIN departamento_academico de ON de.departamento_academico_id=e.departamento_academico_id
          LEFT JOIN rsu_person_affiliation af ON af.persona_id=pp.persona_id LEFT JOIN departamento_academico da ON da.departamento_academico_id=af.departamento_academico_id
          LEFT JOIN administrativo a ON a.persona_id=pp.persona_id LEFT JOIN rsu_authority_profile ap ON ap.persona_id=pp.persona_id AND ap.activo=1
        ) rf ON rf.proyecto_id=p.proyecto_id
        LEFT JOIN rol_proyecto rvp ON rvp.proyecto_id=p.proyecto_id AND rvp.activo=1
        LEFT JOIN catalogo_rol rvrole ON rvrole.rol_id=rvp.rol_id AND LOWER(rvrole.nombre)='voluntario'
        LEFT JOIN persona rv ON rv.persona_id=rvp.persona_id AND rvrole.rol_id IS NOT NULL
        LEFT JOIN rsu_volunteer_registration vr ON vr.proyecto_id=p.proyecto_id AND vr.persona_id=rv.persona_id AND vr.es_actual=1
        WHERE {scope}
        GROUP BY p.proyecto_id,p.titulo,p.fecha_inicio,p.fecha_fin,la.codigo,la.nombre,pa.nombre,w.estado_flujo,w.etapa_actual,w.modalidad,w.fecha_limite_subsanacion,pr.nombres,pr.apellidos,rf.facultad,rr.persona_id
        ORDER BY w.actualizado_en DESC,p.fecha_creacion DESC
        """,
        tuple(params),
    )
    return rows


@router.post("")
async def create_volunteer(data: VolunteerCreateIn, user: AuthUser = Depends(get_current_user)):
    if str(user.rol_sistema or "").lower() == "vicepresidente_academico":
        raise HTTPException(status_code=403, detail="La Vicepresidencia Académica no registra voluntariados.")
    call_id = ensure_annual_volunteer_call(date.today().year)
    call = fetch_one("SELECT * FROM convocatoria WHERE convocatoria_id=%s", (call_id,))
    if not call:
        raise HTTPException(status_code=500, detail="No se pudo resolver la convocatoria anual.")
    responsible = _person(user.persona_id)
    if not _authority("director_facultad", str(responsible.get("facultad") or "")):
        raise HTTPException(status_code=400, detail="La facultad del responsable debe tener un Director de Facultad activo.")
    if not _authority("administrador_rsu") and not _authority("director_rsu"):
        raise HTTPException(status_code=400, detail="Debe existir un administrador RSU activo.")
    collaborator_ids = [item.persona_id for item in data.colaboradores]
    if user.persona_id in collaborator_ids:
        raise HTTPException(status_code=400, detail="El responsable no puede registrarse también como colaborador.")
    for person_id in collaborator_ids:
        _collaborator(person_id)
    project_id = str(uuid4())
    annex1, resolved_address, store_address = _annex1_personal_data(responsible, data, user)
    annex11 = {
        "beneficiario_institucion": data.beneficiario_institucion,
        "beneficiario_facultad": data.beneficiario_facultad,
        "beneficiario_escuela": data.beneficiario_escuela,
        "problemas": data.problemas,
        "necesidades": data.necesidades,
        "objetivo": data.objetivo,
        "actividades": data.actividades,
    }
    signature_path = None
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            _store_missing_personal_address(cur, user.persona_id, resolved_address, store_address)
            cur.execute(
                """INSERT INTO proyecto
                   (proyecto_id,convocatoria_id,periodo_academico_id,linea_accion_id,titulo,tipo_liderazgo,
                    financiado,estado_id,lugar_ejecucion,poblacion_objetivo,resumen,fecha_inicio,fecha_fin,fecha_presentacion)
                   VALUES (%s,%s,%s,%s,%s,'responsable',0,%s,%s,%s,%s,%s,%s,NOW())""",
                (project_id, call_id, call["periodo_academico_id"], data.linea_accion_id, data.titulo,
                 _state_id("voluntariado_pendiente_fichas"), data.beneficiario_institucion,
                 data.beneficiario_institucion, data.objetivo, data.fecha_inicio, data.fecha_fin),
            )
            cur.execute(
                """INSERT INTO rsu_volunteer_workflow
                   (proyecto_id,estado_flujo,etapa_actual,revision_inicial,revision_final)
                   VALUES (%s,'voluntariado_pendiente_fichas','preparacion',1,1)""",
                (project_id,),
            )
            cur.execute(
                """INSERT INTO rol_proyecto
                   (rol_proyecto_id,persona_id,proyecto_id,rol_id,horas_acumuladas,fecha_asignacion,activo)
                   VALUES (%s,%s,%s,%s,0,CURDATE(),1)""",
                (str(uuid4()), user.persona_id, project_id, _role_id("responsable")),
            )
            for person_id in collaborator_ids:
                cur.execute(
                    """INSERT INTO rol_proyecto
                       (rol_proyecto_id,persona_id,proyecto_id,rol_id,horas_acumuladas,fecha_asignacion,activo)
                       VALUES (%s,%s,%s,%s,0,CURDATE(),1)""",
                    (str(uuid4()), person_id, project_id, _role_id("voluntario")),
                )
            _insert_submission(cur, project_id, 1, user.persona_id, annex1, 1)
            _insert_submission(cur, project_id, 11, user.persona_id, annex11, 1)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    if data.firma_responsable:
        signature_path = _store_responsible_signature(project_id, data.firma_responsable, user.persona_id)
    for signature in data.firmas_colaboradores:
        _save_encoded_file(project_id, signature, str(signature.person_id), 'firma_anexo12_previa', f'firmas/{signature.person_id}')
    collaborator_users = []
    for person_id in collaborator_ids:
        account = fetch_one("SELECT usuario_id FROM usuario WHERE persona_id=%s AND activo=1 LIMIT 1", (person_id,))
        if account:
            collaborator_users.append(str(account["usuario_id"]))
    await notify_users(
        collaborator_users, notification_type="voluntariado_ficha_pendiente", title="Ficha de voluntariado pendiente",
        message=f"Completa tu Anexo 12 para el voluntariado «{data.titulo}».", module="Voluntariado",
        priority="alta", entity_type="voluntariado", entity_id=project_id, project_id=project_id, annex_number=12,
        data={"proyecto_id": project_id, "pantalla": "voluntariado_anexo_12"},
        event_key=f"voluntariado:{project_id}:ficha:pendiente", generated_by=user.nombre_completo, important=True,
    )
    return _payload(project_id, user)


@router.get("/{project_id}")
def volunteer_detail(project_id: str, user: AuthUser = Depends(get_current_user)):
    return _payload(project_id, user)


@router.put("/{project_id}/plan")
async def update_volunteer_plan(project_id: str, data: VolunteerCreateIn, user: AuthUser = Depends(get_current_user)):
    project = _assert_owner(project_id, user)
    if project["estado_flujo"] not in {"voluntariado_pendiente_fichas", "voluntariado_observado_inicial"}:
        raise HTTPException(status_code=400, detail="El plan no puede modificarse en la etapa actual.")
    current_ids = {str(item["persona_id"]) for item in _members(project_id)}
    new_ids = {str(item.persona_id) for item in data.colaboradores}
    if user.persona_id in new_ids:
        raise HTTPException(status_code=400, detail="El responsable no puede registrarse también como colaborador.")
    for person_id in new_ids:
        _collaborator(person_id)
    revision = int(project.get("revision_inicial") or 1) + (1 if project["estado_flujo"] == "voluntariado_observado_inicial" else 0)
    responsible = _person(user.persona_id)
    annex1, resolved_address, store_address = _annex1_personal_data(responsible, data, user)
    annex11 = {key: getattr(data, key) for key in ("beneficiario_institucion", "beneficiario_facultad", "beneficiario_escuela", "problemas", "necesidades", "objetivo", "actividades")}
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            _store_missing_personal_address(cur, user.persona_id, resolved_address, store_address)
            for removed in current_ids - new_ids:
                cur.execute("UPDATE rol_proyecto SET activo=0 WHERE proyecto_id=%s AND persona_id=%s", (project_id, removed))
            for added in new_ids - current_ids:
                existing_role = fetch_one(
                    "SELECT rol_proyecto_id FROM rol_proyecto WHERE proyecto_id=%s AND persona_id=%s LIMIT 1",
                    (project_id, added),
                )
                if existing_role:
                    cur.execute(
                        "UPDATE rol_proyecto SET rol_id=%s,activo=1,fecha_asignacion=CURDATE() WHERE rol_proyecto_id=%s",
                        (_role_id("voluntario"), existing_role["rol_proyecto_id"]),
                    )
                else:
                    cur.execute(
                        """INSERT INTO rol_proyecto
                           (rol_proyecto_id,persona_id,proyecto_id,rol_id,horas_acumuladas,fecha_asignacion,activo)
                           VALUES (%s,%s,%s,%s,0,CURDATE(),1)""",
                        (str(uuid4()), added, project_id, _role_id("voluntario")),
                    )
            cur.execute(
                """UPDATE proyecto SET linea_accion_id=%s,titulo=%s,lugar_ejecucion=%s,poblacion_objetivo=%s,
                   resumen=%s,fecha_inicio=%s,fecha_fin=%s,estado_id=%s WHERE proyecto_id=%s""",
                (data.linea_accion_id, data.titulo, data.beneficiario_institucion, data.beneficiario_institucion,
                 data.objetivo, data.fecha_inicio, data.fecha_fin, _state_id("voluntariado_pendiente_fichas"), project_id),
            )
            _insert_submission(cur, project_id, 1, user.persona_id, annex1, revision)
            _insert_submission(cur, project_id, 11, user.persona_id, annex11, revision)
            cur.execute(
                """UPDATE rsu_volunteer_workflow SET estado_flujo='voluntariado_pendiente_fichas',etapa_actual='preparacion',
                   revision_inicial=%s WHERE proyecto_id=%s""",
                (revision, project_id),
            )
            cur.execute("UPDATE rsu_project_review_assignment SET activo=0 WHERE proyecto_id=%s AND anexo_numero=12", (project_id,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    if data.firma_responsable:
        _store_responsible_signature(project_id, data.firma_responsable, user.persona_id)
    _regenerate_bundle(project_id)
    added_ids = sorted(new_ids - current_ids)
    added_users: list[str] = []
    for person_id in added_ids:
        account = fetch_one("SELECT usuario_id FROM usuario WHERE persona_id=%s AND activo=1 LIMIT 1", (person_id,))
        if account:
            added_users.append(str(account["usuario_id"]))
    if added_users:
        await notify_users(
            added_users, notification_type="voluntariado_ficha_pendiente", title="Ficha de voluntariado pendiente",
            message=f"Completa tu Anexo 12 para el voluntariado «{data.titulo}».", module="Voluntariado",
            priority="alta", entity_type="voluntariado", entity_id=project_id, project_id=project_id, annex_number=12,
            data={"proyecto_id": project_id, "pantalla": "voluntariado_anexo_12"},
            event_key=f"voluntariado:{project_id}:fichas:nuevas:r{revision}", generated_by=user.nombre_completo, important=True,
        )
    auto_revision = None
    members_after_update = _members(project_id)
    if members_after_update and all(item.get("estado_anexo12") == "completado" for item in members_after_update):
        auto_revision = await run_in_threadpool(_present_initial_records, project_id)
        if auto_revision is not None:
            await _notify_initial_submission(project_id, auto_revision, user.nombre_completo)
    result = _payload(project_id, user)
    result["envio_automatico"] = auto_revision is not None
    return result


@router.put("/{project_id}/anexo-12")
async def save_registration(project_id: str, data: VolunteerRegistrationIn, user: AuthUser = Depends(get_current_user)):
    project = _assert_access(project_id, user)
    _enforce_correction_deadline(project)
    if project["estado_flujo"] not in {"voluntariado_pendiente_fichas", "voluntariado_observado_inicial"}:
        raise HTTPException(status_code=400, detail="El Anexo 12 no puede modificarse en la etapa actual.")
    _can_write_registration(project_id, data.persona_id, user)
    volunteer_person = _person(data.persona_id)
    volunteer_data = _resolved_volunteer_personal_data(volunteer_person, dict(data.voluntario))
    required = ["nombres", "apellidos", "direccion", "distrito", "provincia", "departamento", "telefono", "email"]
    missing = [name for name in required if not str(volunteer_data.get(name) or "").strip()]
    if missing:
        raise HTTPException(status_code=400, detail="Completa todos los datos personales obligatorios del voluntario.")
    current = fetch_one(
        "SELECT * FROM rsu_volunteer_registration WHERE proyecto_id=%s AND persona_id=%s AND es_actual=1 LIMIT 1",
        (project_id, data.persona_id),
    )
    revision = int((current or {}).get("revision") or 0) + 1
    photo = _save_encoded_file(project_id, data.foto, data.persona_id, "foto_anexo12", f"anexo12/{data.persona_id}")
    if data.firma.workflow_file_id and not data.firma.data_base64:
        previous = fetch_one(
            """SELECT archivo_url FROM rsu_workflow_file WHERE workflow_file_id=%s AND proyecto_id=%s
               AND persona_firmante_id=%s AND proposito='firma_anexo12_previa' LIMIT 1""",
            (data.firma.workflow_file_id, project_id, data.persona_id),
        )
        if not previous or not Path(str(previous['archivo_url'])).is_file():
            raise HTTPException(400, 'La firma seleccionada no corresponde al voluntario o no existe.')
        signature = str(previous['archivo_url'])
    else:
        signature = _save_encoded_file(project_id, data.firma, data.persona_id, "firma_anexo12", f"anexo12/{data.persona_id}")
    fingerprint = _save_encoded_file(project_id, data.huella, data.persona_id, "huella_anexo12", f"anexo12/{data.persona_id}")
    responsible = _responsible(project_id)
    responsible_signature = _signature_path(project_id, str(responsible["persona_id"]))
    output = _root() / project_id / "anexo12" / data.persona_id / f"anexo_12_r{revision}.pdf"
    await run_in_threadpool(
        generate_annex12_pdf,
        output,
        volunteer=volunteer_data,
        tutor=data.tutor,
        insurance=data.seguro,
        disability=data.discapacidad,
        photo_path=photo,
        volunteer_signature_path=signature,
        fingerprint_path=fingerprint,
        responsible=responsible,
        responsible_signature_path=responsible_signature,
        submitted_at=date.today(),
    )
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            _store_missing_volunteer_personal_data(cur, data.persona_id, volunteer_person, volunteer_data)
            cur.execute("UPDATE rsu_volunteer_registration SET es_actual=0 WHERE proyecto_id=%s AND persona_id=%s", (project_id, data.persona_id))
            cur.execute(
                """INSERT INTO rsu_volunteer_registration
                   (registration_id,proyecto_id,persona_id,revision,datos_tutor_json,datos_voluntario_json,
                    seguro_json,discapacidad_json,foto_url,firma_url,huella_url,pdf_url,estado,es_actual,fecha_presentacion)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'completado',1,NOW())""",
                (str(uuid4()), project_id, data.persona_id, revision, json.dumps(data.tutor, ensure_ascii=False),
                 json.dumps(volunteer_data, ensure_ascii=False), json.dumps(data.seguro, ensure_ascii=False),
                 json.dumps(data.discapacidad, ensure_ascii=False), photo, signature, fingerprint, str(output)),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    bundle = await run_in_threadpool(_regenerate_bundle, project_id)
    await notify_project_responsibles(
        project_id, notification_type="voluntariado_ficha_completada", title="Anexo 12 completado",
        message=f"{volunteer_person['nombre_completo']} completó su ficha de registro.", module="Voluntariado",
        priority="informativa", entity_type="voluntariado", entity_id=project_id, annex_number=12,
        data={"proyecto_id": project_id, "persona_id": data.persona_id, "pantalla": "expediente_voluntariado"},
        event_key=f"voluntariado:{project_id}:ficha:{data.persona_id}:r{revision}", generated_by=user.nombre_completo,
    )
    auto_revision = None
    members_after_save = _members(project_id)
    if members_after_save and all(item.get("estado_anexo12") == "completado" for item in members_after_save):
        auto_revision = await run_in_threadpool(_present_initial_records, project_id)
        if auto_revision is not None:
            await _notify_initial_submission(project_id, auto_revision, user.nombre_completo)
    result = _payload(project_id, user)
    result["bundle_generado"] = bundle
    result["envio_automatico"] = auto_revision is not None
    return result


def _present_initial_records(project_id: str) -> int | None:
    """Presenta automáticamente los Anexos 1, 11 y 12 cuando todas las fichas están completas.

    Devuelve la revisión presentada. Si otro proceso ya realizó la presentación,
    devuelve ``None`` para mantener la operación idempotente.
    """
    with _INITIAL_SUBMISSION_LOCK:
        project = _project_row(project_id)
        _enforce_correction_deadline(project)
        current_state = str(project.get("estado_flujo") or "")
        if current_state == "voluntariado_en_evaluacion_inicial":
            return None
        if current_state not in {"voluntariado_pendiente_fichas", "voluntariado_observado_inicial"}:
            raise HTTPException(status_code=400, detail="El expediente no está listo para una presentación inicial.")
        members = _members(project_id)
        if not members or any(item.get("estado_anexo12") != "completado" for item in members):
            raise HTTPException(status_code=400, detail="Todos los colaboradores deben completar el Anexo 12 antes de presentar el expediente.")
        bundle = fetch_one(
            "SELECT * FROM rsu_volunteer_annex12_bundle WHERE proyecto_id=%s AND es_actual=1 LIMIT 1",
            (project_id,),
        )
        if not bundle:
            raise HTTPException(status_code=400, detail="No se pudo generar el Anexo 12 consolidado.")
        responsible = _responsible(project_id)
        revision = int(project.get("revision_inicial") or 1)
        if current_state == "voluntariado_observado_inicial":
            revision += 1
        current_a1 = _current_submission(project_id, 1)
        current_a11 = _current_submission(project_id, 11)
        with get_connection() as conn:
            cur = conn.cursor()
            try:
                if current_a1 and int(current_a1.get("revision") or 0) < revision:
                    _insert_submission(cur, project_id, 1, responsible["persona_id"], current_a1["contenido"], revision)
                if current_a11 and int(current_a11.get("revision") or 0) < revision:
                    _insert_submission(cur, project_id, 11, responsible["persona_id"], current_a11["contenido"], revision)
                _insert_submission(
                    cur,
                    project_id,
                    12,
                    responsible["persona_id"],
                    {
                        "bundle_id": bundle["bundle_id"],
                        "archivo_url": bundle["archivo_url"],
                        "cantidad_fichas": bundle["cantidad_fichas"],
                        "sha256": bundle["sha256"],
                    },
                    revision,
                )
                cur.execute(
                    "UPDATE rsu_project_review_assignment SET activo=0 WHERE proyecto_id=%s AND anexo_numero=12",
                    (project_id,),
                )
                _assign_reviewers(cur, project_id, 12, revision)
                cur.execute(
                    """UPDATE rsu_volunteer_workflow
                       SET estado_flujo='voluntariado_en_evaluacion_inicial',etapa_actual='evaluacion_inicial',
                           revision_inicial=%s,fecha_presentacion_inicial=NOW(),fecha_limite_subsanacion=NULL
                       WHERE proyecto_id=%s""",
                    (revision, project_id),
                )
                cur.execute(
                    "UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s",
                    (_state_id("voluntariado_en_evaluacion_inicial"), project_id),
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                cur.close()
        return revision


async def _notify_initial_submission(project_id: str, revision: int, generated_by: str) -> None:
    project = _project_row(project_id)
    common = {
        "notification_type": "voluntariado_presentado",
        "title": "Voluntariado presentado",
        "module": "Voluntariado",
        "priority": "alta",
        "entity_type": "voluntariado",
        "entity_id": project_id,
        "project_id": project_id,
        "annex_number": 12,
        "data": {"proyecto_id": project_id, "pantalla": "expediente_voluntariado"},
        "generated_by": generated_by,
        "important": True,
    }
    await notify_admins(
        message=f"El voluntariado «{project['titulo']}» completó todos los Anexos 12 y requiere evaluación de los Anexos 1, 11 y 12.",
        event_key=f"voluntariado:{project_id}:inicial:r{revision}",
        **common,
    )
    director = _authority("director_facultad", str(_responsible(project_id).get("facultad") or ""))
    if director and director.get("usuario_id"):
        await notify_users(
            [str(director["usuario_id"])],
            message=f"El voluntariado «{project['titulo']}» completó su expediente y requiere su evaluación.",
            event_key=f"voluntariado:{project_id}:director:inicial:r{revision}",
            **common,
        )
    await notify_project_responsibles(
        project_id,
        notification_type="voluntariado_envio_automatico",
        title="Expediente remitido automáticamente",
        message=f"El último colaborador completó el Anexo 12. Los Anexos 1, 11 y 12 de «{project['titulo']}» fueron enviados a evaluación.",
        module="Voluntariado",
        priority="alta",
        entity_type="voluntariado",
        entity_id=project_id,
        annex_number=12,
        data={"proyecto_id": project_id, "pantalla": "expediente_voluntariado"},
        event_key=f"voluntariado:{project_id}:responsable:inicial:r{revision}",
        generated_by=generated_by,
        important=True,
    )


@router.post("/{project_id}/presentar")
async def submit_initial(project_id: str, user: AuthUser = Depends(get_current_user)):
    _assert_owner(project_id, user)
    revision = await run_in_threadpool(_present_initial_records, project_id)
    if revision is not None:
        await _notify_initial_submission(project_id, revision, user.nombre_completo)
    result = _payload(project_id, user)
    result["envio_automatico"] = False
    result["ya_presentado"] = revision is None
    return result


async def _evaluate(project_id: str, annex: int, data: VolunteerEvaluationIn, reviewer: AuthUser) -> dict[str, Any]:
    project = _assert_access(project_id, reviewer)
    assignment = _assignment(project_id, annex, reviewer)
    if not assignment:
        raise HTTPException(status_code=403, detail="No tienes una evaluación pendiente para este expediente.")
    revision = int(assignment.get("revision") or 1)
    deadline = _add_business_days(date.today(), 3) if data.resultado == "observado" else None
    evaluation_id = str(uuid4())
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO rsu_annex_evaluation
                   (evaluation_id,proyecto_id,anexo_numero,revision,evaluador_persona_id,criterios_json,total_si,total_no,
                    resultado,observaciones,fecha_limite_subsanacion,rol_revisor,facultad_revisor)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (evaluation_id, project_id, annex, revision, reviewer.persona_id,
                 json.dumps([{"clave": "revision_documental", "cumple": data.resultado == "aprobado"}], ensure_ascii=False),
                 1 if data.resultado == "aprobado" else 0, 1 if data.resultado == "observado" else 0,
                 data.resultado, data.observaciones, deadline, assignment["rol_revisor"], assignment.get("facultad")),
            )
            cur.execute(
                """UPDATE rsu_project_review_assignment SET estado=%s,evaluation_id=%s,fecha_revision=NOW(),revisor_persona_id=%s
                   WHERE assignment_id=%s""",
                (data.resultado, evaluation_id, reviewer.persona_id, assignment["assignment_id"]),
            )
            if data.resultado == "observado":
                flow = "voluntariado_observado_inicial" if annex == 12 else "voluntariado_observado_final"
                stage = "subsanacion_inicial" if annex == 12 else "subsanacion_final"
                cur.execute(
                    "UPDATE rsu_volunteer_workflow SET estado_flujo=%s,etapa_actual=%s,fecha_limite_subsanacion=%s WHERE proyecto_id=%s",
                    (flow, stage, deadline, project_id),
                )
                cur.execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id(flow), project_id))
                cur.execute(
                    """UPDATE rsu_project_review_assignment SET activo=0
                       WHERE proyecto_id=%s AND anexo_numero=%s AND revision=%s AND assignment_id<>%s""",
                    (project_id, annex, revision, assignment["assignment_id"]),
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    if data.resultado == "observado":
        await notify_project_responsibles(
            project_id, notification_type="voluntariado_observado", title="Voluntariado observado",
            message=f"El expediente «{project['titulo']}» debe subsanarse hasta el {deadline.strftime('%d/%m/%Y')}.",
            module="Voluntariado", priority="alta", entity_type="voluntariado", entity_id=project_id,
            annex_number=annex, data={"proyecto_id": project_id, "fecha_limite": deadline.isoformat(), "observaciones": data.observaciones},
            event_key=f"voluntariado:{project_id}:anexo:{annex}:r{revision}:observado:{assignment['rol_revisor']}",
            generated_by=reviewer.nombre_completo, important=True,
        )
        return _payload(project_id, reviewer)
    pending = fetch_one(
        """SELECT COUNT(*) AS total FROM rsu_project_review_assignment
           WHERE proyecto_id=%s AND anexo_numero=%s AND revision=%s AND activo=1 AND estado='pendiente'""",
        (project_id, annex, revision),
    )
    if int((pending or {}).get("total") or 0) == 0:
        kind = "aprobacion_inicial" if annex == 12 else "aprobacion_final"
        memo = await run_in_threadpool(_issue_memorandum, project_id, kind, reviewer)
        with get_connection() as conn:
            cur = conn.cursor()
            try:
                if annex == 12:
                    cur.execute(
                        """UPDATE rsu_volunteer_workflow SET estado_flujo='voluntariado_memorandum_inicial',etapa_actual='lista_ejecucion',
                           fecha_aprobacion_inicial=NOW(),fecha_memorandum_inicial=NOW(),fecha_limite_subsanacion=NULL WHERE proyecto_id=%s""",
                        (project_id,),
                    )
                    state = "voluntariado_memorandum_inicial"
                else:
                    cur.execute(
                        """UPDATE rsu_volunteer_workflow SET estado_flujo='voluntariado_en_certificacion',etapa_actual='certificacion',
                           fecha_aprobacion_final=NOW(),fecha_memorandum_final=NOW(),fecha_limite_subsanacion=NULL WHERE proyecto_id=%s""",
                        (project_id,),
                    )
                    state = "voluntariado_en_certificacion"
                cur.execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id(state), project_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
        recipients = project_user_ids(project_id, roles=["responsable"])
        vice = _authority("vicepresidente_academico")
        if vice and vice.get("usuario_id"):
            recipients.append(str(vice["usuario_id"]))
        await notify_users(
            recipients, notification_type="voluntariado_aprobado", title="Voluntariado aprobado",
            message=f"El expediente «{project['titulo']}» fue aprobado. Memorándum {memo.get('numero_memorandum')} disponible.",
            module="Voluntariado", priority="alta", entity_type="voluntariado", entity_id=project_id, project_id=project_id,
            annex_number=annex, data={"proyecto_id": project_id, "memorandum_id": memo.get("memorandum_id")},
            event_key=f"voluntariado:{project_id}:anexo:{annex}:r{revision}:aprobado", generated_by=reviewer.nombre_completo, important=True,
        )
    return _payload(project_id, reviewer)


@router.post("/{project_id}/evaluar-inicial")
async def evaluate_initial(project_id: str, data: VolunteerEvaluationIn, user: AuthUser = Depends(get_current_user)):
    return await _evaluate(project_id, 12, data, user)


@router.post("/{project_id}/iniciar-ejecucion")
async def start_execution(project_id: str, data: VolunteerStartIn, user: AuthUser = Depends(get_current_user)):
    project = _assert_owner(project_id, user)
    if project["estado_flujo"] != "voluntariado_memorandum_inicial":
        raise HTTPException(status_code=400, detail="El voluntariado debe contar con el memorándum inicial antes de ejecutarse.")
    state = "voluntariado_en_ejecucion_interno" if data.modalidad == "interno" else "voluntariado_en_ejecucion_externo"
    execute(
        """UPDATE rsu_volunteer_workflow SET estado_flujo=%s,etapa_actual='ejecucion',modalidad=%s,
           fecha_inicio_ejecucion=%s WHERE proyecto_id=%s""",
        (state, data.modalidad, data.fecha_inicio_real or date.today(), project_id),
    )
    execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id(state), project_id))
    await notify_admins(
        notification_type="voluntariado_en_ejecucion", title="Voluntariado en ejecución",
        message=f"«{project['titulo']}» inició como voluntariado {data.modalidad}.", module="Voluntariado",
        entity_type="voluntariado", entity_id=project_id, project_id=project_id,
        data={"proyecto_id": project_id, "modalidad": data.modalidad},
        event_key=f"voluntariado:{project_id}:inicio:{data.modalidad}", generated_by=user.nombre_completo,
    )
    return _payload(project_id, user)


@router.post("/{project_id}/anexo-13")
async def submit_closure(project_id: str, data: VolunteerClosureIn, user: AuthUser = Depends(get_current_user)):
    project = _assert_owner(project_id, user)
    if project["estado_flujo"] not in {"voluntariado_en_ejecucion_interno", "voluntariado_en_ejecucion_externo", "voluntariado_observado_final"}:
        raise HTTPException(status_code=400, detail="El informe final no puede presentarse en la etapa actual.")
    revision = int(project.get("revision_final") or 1) + (1 if project["estado_flujo"] == "voluntariado_observado_final" else 0)
    evidence_paths: list[tuple[str, str]] = []
    for item in data.evidencias:
        path = _save_encoded_file(project_id, item, user.persona_id, "evidencia_anexo13", "anexo13/evidencias")
        evidence_paths.append((item.label or item.filename, path))
    signature_path = _store_responsible_signature(project_id, data.firma_responsable, user.persona_id)
    content = data.model_dump(mode="json", exclude={"evidencias", "firma_responsable"})
    output = _root() / project_id / "anexo13" / f"anexo_13_r{revision}.pdf"
    await run_in_threadpool(
        generate_annex13_pdf,
        output,
        responsible=_responsible(project_id),
        project=project,
        content=content,
        members=_members(project_id),
        signature_path=signature_path,
        evidence_paths=evidence_paths,
    )
    content["pdf_url"] = str(output)
    content["evidencias"] = [{"etiqueta": label, "archivo_url": path} for label, path in evidence_paths]
    external = project.get("modalidad") == "externo"
    flow = "voluntariado_en_convalidacion" if external else "voluntariado_en_evaluacion_final"
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            _insert_submission(cur, project_id, 13, user.persona_id, content, revision)
            cur.execute("UPDATE rsu_project_review_assignment SET activo=0 WHERE proyecto_id=%s AND anexo_numero=13", (project_id,))
            _assign_reviewers(cur, project_id, 13, revision, external=external)
            cur.execute(
                """UPDATE rsu_volunteer_workflow SET estado_flujo=%s,etapa_actual='evaluacion_final',revision_final=%s,
                   fecha_presentacion_final=NOW(),fecha_limite_subsanacion=NULL WHERE proyecto_id=%s""",
                (flow, revision, project_id),
            )
            cur.execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id(flow), project_id))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    await notify_admins(
        notification_type="voluntariado_informe_final", title="Informe final presentado",
        message=f"El Anexo 13 de «{project['titulo']}» está listo para {'convalidación' if external else 'evaluación'}.",
        module="Voluntariado", priority="alta", entity_type="voluntariado", entity_id=project_id, project_id=project_id,
        annex_number=13, data={"proyecto_id": project_id, "modalidad": project.get("modalidad")},
        event_key=f"voluntariado:{project_id}:final:r{revision}", generated_by=user.nombre_completo, important=True,
    )
    return _payload(project_id, user)


@router.post("/{project_id}/evaluar-final")
async def evaluate_final(project_id: str, data: VolunteerEvaluationIn, user: AuthUser = Depends(get_current_user)):
    return await _evaluate(project_id, 13, data, user)


@router.post("/{project_id}/certificar")
async def certify(project_id: str, data: VolunteerCertificateIn, admin: AuthUser = Depends(require_admin)):
    project = _assert_access(project_id, admin)
    deny_certificate_for_simulation(project_id)
    if project["estado_flujo"] != "voluntariado_en_certificacion":
        raise HTTPException(status_code=400, detail="El voluntariado no está habilitado para certificación.")
    people = [_responsible(project_id)] + _members(project_id)
    emitted = data.fecha_emision or date.today()
    director = _authority("director_rsu") or _authority("administrador_rsu") or {}
    output_dir = _root() / project_id / "certificados"
    prepared: list[dict[str, Any]] = []
    batch: list[tuple[Path, dict[str, Any]]] = []
    for person in people:
        certificate_id = str(uuid4())
        output = output_dir / f"anexo_14_{person['persona_id']}.pdf"
        certificate_data = {
            "nombre_completo": person.get("nombre_completo"),
            "dni": person.get("dni"),
            "titulo_proyecto": project.get("titulo"),
            "tipo_proyecto": "Voluntariado RSU",
            "rol": "Responsable" if person["persona_id"] == _responsible(project_id)["persona_id"] else "Voluntario",
            "horas": data.horas_certificadas,
            "fecha_emision": emitted,
            "codigo_verificacion": f"VOL-{uuid4().hex[:12].upper()}",
            "director_nombre": director.get("nombre_completo"),
            "director_cargo": director.get("cargo"),
            "firma_director": director.get("firma_url"),
        }
        batch.append((output, certificate_data))
        prepared.append({"certificado_id": certificate_id, "persona": person, "output": output, "codigo": certificate_data["codigo_verificacion"]})
    await run_in_threadpool(generate_certificate_pdfs_batch, batch)
    state = fetch_one("SELECT catalogo_estado_id FROM catalogo_estado WHERE entidad='certificado' AND LOWER(nombre)='emitido' LIMIT 1")
    if not state:
        execute("INSERT INTO catalogo_estado (catalogo_estado_id,entidad,nombre,descripcion,activo) VALUES (UUID(),'certificado','emitido','Certificado emitido',1)")
        state = fetch_one("SELECT catalogo_estado_id FROM catalogo_estado WHERE entidad='certificado' AND LOWER(nombre)='emitido' LIMIT 1")
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            for item in prepared:
                existing = fetch_one("SELECT certificado_id FROM certificado WHERE proyecto_id=%s AND persona_id=%s LIMIT 1", (project_id, item["persona"]["persona_id"]))
                if existing:
                    cur.execute(
                        """UPDATE certificado SET horas_certificadas=%s,archivo_url=%s,fecha_emision=%s,codigo_verificacion=%s,estado_id=%s
                           WHERE certificado_id=%s""",
                        (data.horas_certificadas, str(item["output"]), emitted, item["codigo"], state["catalogo_estado_id"], existing["certificado_id"]),
                    )
                    item["certificado_id"] = existing["certificado_id"]
                else:
                    cur.execute(
                        """INSERT INTO certificado
                           (certificado_id,persona_id,proyecto_id,horas_certificadas,archivo_url,fecha_solicitud,fecha_emision,codigo_verificacion,es_convalidacion,estado_id)
                           VALUES (%s,%s,%s,%s,%s,CURDATE(),%s,%s,%s,%s)""",
                        (item["certificado_id"], item["persona"]["persona_id"], project_id, data.horas_certificadas,
                         str(item["output"]), emitted, item["codigo"], int(project.get("modalidad") == "externo"), state["catalogo_estado_id"]),
                    )
            cur.execute(
                "UPDATE rol_proyecto SET horas_acumuladas=%s WHERE proyecto_id=%s AND activo=1",
                (data.horas_certificadas, project_id),
            )
            cur.execute(
                """UPDATE rsu_volunteer_workflow SET estado_flujo='voluntariado_certificado',etapa_actual='finalizada',
                   fecha_certificacion=NOW() WHERE proyecto_id=%s""",
                (project_id,),
            )
            cur.execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id("voluntariado_certificado"), project_id))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    for item in prepared:
        account = fetch_one("SELECT usuario_id FROM usuario WHERE persona_id=%s AND activo=1 LIMIT 1", (item["persona"]["persona_id"],))
        if account:
            await notify_users(
                [str(account["usuario_id"])], notification_type="voluntariado_certificado", title="Certificado disponible",
                message=f"Tu certificado del voluntariado «{project['titulo']}» está disponible.", module="Voluntariado",
                priority="alta", entity_type="certificado", entity_id=item["certificado_id"], project_id=project_id,
                data={"proyecto_id": project_id, "certificado_id": item["certificado_id"]},
                event_key=f"voluntariado:{project_id}:certificado:{item['persona']['persona_id']}", generated_by=admin.nombre_completo, important=True,
            )
    return _payload(project_id, admin)


def _pdf_path(project_id: str, annex: int) -> Path:
    project = _project_row(project_id)
    responsible = _responsible(project_id)
    signature = _signature_path(project_id, str(responsible["persona_id"]))
    if annex == 1:
        submission = _current_submission(project_id, 1)
        if not submission:
            raise HTTPException(status_code=404, detail="El Anexo 1 no está disponible.")
        output = _root() / project_id / "anexos" / f"anexo_1_r{submission['revision']}.pdf"
        generate_annex1_pdf(output, responsible=responsible, project=project, content=submission["contenido"], signature_path=signature)
        return output
    if annex == 11:
        submission = _current_submission(project_id, 11)
        if not submission:
            raise HTTPException(status_code=404, detail="El Anexo 11 no está disponible.")
        output = _root() / project_id / "anexos" / f"anexo_11_r{submission['revision']}.pdf"
        generate_annex11_pdf(output, responsible=responsible, project=project, content=submission["contenido"], members=_members(project_id), signature_path=signature)
        return output
    if annex == 12:
        bundle = fetch_one("SELECT archivo_url FROM rsu_volunteer_annex12_bundle WHERE proyecto_id=%s AND es_actual=1 LIMIT 1", (project_id,))
        if not bundle or not Path(str(bundle["archivo_url"])).exists():
            raise HTTPException(status_code=404, detail="El Anexo 12 consolidado no está disponible.")
        return Path(str(bundle["archivo_url"]))
    if annex == 13:
        submission = _current_submission(project_id, 13)
        if not submission:
            raise HTTPException(status_code=404, detail="El Anexo 13 no está disponible.")
        path = Path(str(submission["contenido"].get("pdf_url") or ""))
        if not path.exists():
            raise HTTPException(status_code=404, detail="El PDF del Anexo 13 no está disponible.")
        return path
    raise HTTPException(status_code=404, detail="El anexo solicitado no está disponible.")


@router.get("/{project_id}/anexos/{annex_number}/pdf")
async def download_annex(project_id: str, annex_number: int, user: AuthUser = Depends(get_current_user)):
    _assert_access(project_id, user)
    path = await run_in_threadpool(_pdf_path, project_id, int(annex_number))
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/{project_id}/memorandums/{kind}")
def download_memorandum(project_id: str, kind: str, user: AuthUser = Depends(get_current_user)):
    _assert_access(project_id, user)
    aliases = {"inicial": "aprobacion_inicial", "final": "aprobacion_final", "aprobacion_inicial": "aprobacion_inicial", "aprobacion_final": "aprobacion_final"}
    normalized = aliases.get(kind)
    if not normalized:
        raise HTTPException(status_code=400, detail="Tipo de memorándum no válido.")
    row = fetch_one("SELECT archivo_pdf_url FROM rsu_volunteer_memorandum WHERE proyecto_id=%s AND tipo=%s LIMIT 1", (project_id, normalized))
    if not row or not Path(str(row["archivo_pdf_url"])).exists():
        raise HTTPException(status_code=404, detail="El memorándum no está disponible.")
    path = Path(str(row["archivo_pdf_url"]))
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/certificados/{certificate_id}/descargar")
def download_certificate(certificate_id: str, user: AuthUser = Depends(get_current_user)):
    row = fetch_one(
        """SELECT c.*,CONCAT_WS(' ',pe.nombres,pe.apellidos) AS nombre_completo,pr.titulo
           FROM certificado c
           INNER JOIN persona pe ON pe.persona_id=c.persona_id
           INNER JOIN proyecto pr ON pr.proyecto_id=c.proyecto_id
           WHERE c.certificado_id=%s LIMIT 1""",
        (certificate_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="El certificado no existe.")
    _assert_access(str(row["proyecto_id"]), user)
    role = str(user.rol_sistema or "").lower()
    if role not in {"administrador_rsu", "director_rsu", "vicepresidente_academico"} and str(row["persona_id"]) != str(user.persona_id):
        responsible = _responsible(str(row["proyecto_id"]))
        if str(responsible["persona_id"]) != str(user.persona_id):
            raise HTTPException(status_code=403, detail="No puedes descargar el certificado de otra persona.")
    path = Path(str(row["archivo_url"]))
    if not path.exists():
        raise HTTPException(status_code=404, detail="El archivo del certificado no está disponible.")
    filename = certificate_download_filename(row.get("nombre_completo"), row.get("titulo"))
    return FileResponse(path, media_type="application/pdf", filename=filename)
