from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import re
import shutil
import threading
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, model_validator

from app.api.deps import get_current_user, require_admin, require_project_reviewer, deny_certificate_for_simulation
from app.core.config import get_settings
from app.db.mysql import execute, fetch_all, fetch_one, get_connection
from app.db.specific_activity_schema import ensure_annual_specific_activity_call
from app.schemas.auth import AuthUser
from app.schemas.project_lifecycle import EncodedFileIn
from app.services.certificate_pdf import generate_certificate_pdfs_batch
from app.services.download_filenames import certificate_download_filename
from app.services.memorandum_service import generate_certificate_memorandum, generate_memorandum
from app.services.notification_service import notify_admins, notify_project_responsibles, notify_users, project_user_ids
from app.services.specific_activity_documents import generate_activity_pdf


router = APIRouter(prefix="/actividades-especificas", tags=["Actividades específicas"])

_MEMORANDUM_LOCKS: dict[str, threading.RLock] = {}
_MEMORANDUM_LOCKS_GUARD = threading.Lock()


def _memorandum_lock(project_id: str, kind: str) -> threading.RLock:
    key = f"{project_id}:{kind}"
    with _MEMORANDUM_LOCKS_GUARD:
        lock = _MEMORANDUM_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _MEMORANDUM_LOCKS[key] = lock
        return lock



INITIAL_CRITERIA = [
    ("plazo_ejecucion", "Plazo de ejecución"),
    ("lugar_ejecucion", "Lugar de ejecución de la actividad"),
    ("resumen", "Resumen de las actividades"),
    ("problematica", "Problema a abordar según la línea de acción"),
    ("objetivo", "Objetivo"),
    ("metodologia", "Metodología o estrategias"),
    ("impacto_social", "Impacto social"),
    ("medio_difusion", "Medio de difusión"),
    ("financiamiento", "Financiamiento"),
]

# El Anexo 10 no cuenta con una ficha de evaluación adicional. La revisión
# final se registra como una conformidad integral o una observación general.
FINAL_CRITERIA = [
    ("informe_final", "Revisión integral del Anexo 10"),
]


class ActivityMemberIn(BaseModel):
    persona_id: str
    rol: Literal["corresponsable", "colaborador", "docente_asesor"]


class ActivityCreateIn(BaseModel):
    titulo: str = Field(min_length=3, max_length=255)
    linea_accion_id: str
    fecha_inicio: date
    fecha_fin: date
    horas_totales: int = Field(ge=16, le=2000)
    domicilio: str | None = None
    condicion: str | None = None
    lugar_entorno: str = Field(min_length=3)
    resumen: str = Field(min_length=10)
    problematica: str = Field(min_length=10)
    objetivo: str = Field(min_length=5)
    metodologia: str = Field(min_length=10)
    impacto_social: str = Field(min_length=10)
    medio_difusion: str = Field(min_length=3)
    financiamiento: str = Field(min_length=2)
    miembros: list[ActivityMemberIn] = Field(default_factory=list)
    firmas: list[EncodedFileIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("La fecha de término no puede ser anterior a la fecha de inicio.")
        return self


class CriterionIn(BaseModel):
    clave: str
    cumple: bool
    observacion: str | None = None


class ActivityEvaluationIn(BaseModel):
    criterios: list[CriterionIn]
    observaciones: str | None = None


class ActivityStartIn(BaseModel):
    fecha_inicio_real: date | None = None


class ActivityClosureIn(BaseModel):
    fecha_inicio: date
    fecha_fin: date
    horas_totales: int = Field(ge=16, le=2000)
    lugar_entorno: str = Field(min_length=3)
    resumen: str = Field(min_length=10)
    problematica: str = Field(min_length=10)
    objetivo: str = Field(min_length=5)
    metodologia: str = Field(min_length=10)
    impacto_social: str = Field(min_length=10)
    evidencias_descripcion: str = Field(min_length=3)
    financiamiento: str = Field(min_length=2)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("La fecha de término no puede ser anterior a la fecha de inicio.")
        return self


class ActivityCertificateIn(BaseModel):
    fecha_emision: date | None = None
    horas_certificadas: int | None = Field(default=None, ge=16, le=2000)


def _settings_root() -> Path:
    settings = get_settings()
    root = Path(settings.upload_dir) / "private_specific_activities"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9áéíóúÁÉÍÓÚñÑ_-]+", "_", str(value or "actividad").strip())
    return text.strip("_")[:100] or "actividad"


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


def _person_type(person_id: str) -> str:
    row = fetch_one(
        """
        SELECT CASE WHEN d.persona_id IS NOT NULL THEN 'docente'
                    WHEN e.persona_id IS NOT NULL THEN 'estudiante'
                    WHEN a.persona_id IS NOT NULL THEN 'administrativo'
                    WHEN af.tipo_vinculo IS NOT NULL THEN LOWER(af.tipo_vinculo)
                    ELSE 'persona' END AS tipo
        FROM persona p
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        WHERE p.persona_id=%s AND p.activo=1 LIMIT 1
        """,
        (person_id,),
    )
    if not row:
        raise HTTPException(status_code=400, detail="La persona seleccionada no existe o está inactiva.")
    return str(row["tipo"] or "persona").lower()


def _person_faculty(person_id: str) -> str:
    row = fetch_one(
        """
        SELECT COALESCE(dd.facultad,de.facultad,da.facultad,ap.facultad,a.area,'') AS facultad
        FROM persona p
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN departamento_academico dd ON dd.departamento_academico_id=d.departamento_academico_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN departamento_academico de ON de.departamento_academico_id=e.departamento_academico_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        LEFT JOIN departamento_academico da ON da.departamento_academico_id=af.departamento_academico_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
        WHERE p.persona_id=%s LIMIT 1
        """,
        (person_id,),
    )
    return str((row or {}).get("facultad") or "").strip()


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
               sr.codigo AS rol_sistema,COALESCE(usr.facultad,ap.facultad,'') AS facultad,
               COALESCE(ap.cargo_oficial,a.cargo,sr.nombre) AS cargo,
               ap.firma_url
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


def _validate_activity_file(item: EncodedFileIn) -> tuple[bytes, str]:
    if item.workflow_file_id:
        return b"", ""
    try:
        content = base64.b64decode(str(item.data_base64 or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"La firma {item.label} no tiene un formato válido.") from exc
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"La firma {item.label} está vacía o supera 10 MB.")
    extension = Path(item.filename).suffix.lower()
    if extension not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=400, detail="Las firmas deben ser imágenes PNG, JPG, JPEG o WEBP.")
    return content, extension


def _store_activity_signatures(cur, project_id: str, files: list[EncodedFileIn], uploader: str) -> list[Path]:
    created: list[Path] = []
    base = _settings_root() / project_id / "firmas"
    base.mkdir(parents=True, exist_ok=True)
    for item in files:
        if item.workflow_file_id:
            current = fetch_one(
                "SELECT workflow_file_id FROM rsu_workflow_file WHERE workflow_file_id=%s AND proyecto_id=%s LIMIT 1",
                (item.workflow_file_id, project_id),
            )
            if not current:
                raise HTTPException(status_code=400, detail="La firma guardada no pertenece a esta actividad.")
            continue
        content, extension = _validate_activity_file(item)
        file_id = str(uuid4())
        output = base / f"{file_id}{extension}"
        output.write_bytes(content)
        created.append(output)
        label_lower = str(item.label or "").lower()
        if "asesor" in label_lower:
            purpose = "firma_asesor_actividad"
        elif "corresponsable" in label_lower:
            purpose = "firma_corresponsable_actividad"
        else:
            purpose = "firma_responsable_actividad"
        cur.execute(
            """INSERT INTO rsu_workflow_file
               (workflow_file_id,proyecto_id,anexo_numero,proposito,etiqueta,persona_firmante_id,
                archivo_url,nombre_archivo,mime_type,subido_por)
               VALUES (%s,%s,8,%s,%s,%s,%s,%s,%s,%s)""",
            (
                file_id, project_id, purpose, item.label, item.person_id or uploader,
                str(output), item.filename,
                item.mime_type or mimetypes.guess_type(item.filename)[0] or "application/octet-stream",
                uploader,
            ),
        )
    return created


def _activity_signatures(project_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        """SELECT workflow_file_id,persona_firmante_id,proposito,etiqueta,archivo_url,nombre_archivo,mime_type,fecha_subida
           FROM rsu_workflow_file
           WHERE proyecto_id=%s AND proposito IN ('firma_responsable_actividad','firma_corresponsable_actividad','firma_asesor_actividad')
           ORDER BY fecha_subida DESC""",
        (project_id,),
    )


def _team(project_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT rp.persona_id,cr.nombre AS rol,
               CASE cr.nombre WHEN 'docente_asesor' THEN 'Docente asesor' ELSE cr.nombre END AS rol_label,
               CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo,p.dni,
               COALESCE(c.email_institucional,c.email_personal,'') AS email,
               c.direccion AS domicilio,
               COALESCE(dd.facultad,de.facultad,da.facultad,a.area,ap.facultad,'') AS facultad,
               COALESCE(dd.carrera,de.carrera,da.carrera,'') AS escuela,
               COALESCE(e.codigo_estudiante,af.codigo_institucional,'') AS codigo,
               CASE WHEN d.persona_id IS NOT NULL THEN 'Docente'
                    WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                    WHEN a.persona_id IS NOT NULL THEN 'Administrativo'
                    WHEN af.tipo_vinculo IS NOT NULL THEN af.tipo_vinculo ELSE 'Persona' END AS tipo_persona,
               CASE WHEN d.persona_id IS NOT NULL THEN COALESCE(d.condicion,'Docente')
                    WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                    WHEN a.persona_id IS NOT NULL THEN COALESCE(a.cargo,'Administrativo')
                    ELSE COALESCE(af.tipo_vinculo,'Persona') END AS condicion,
               COALESCE((
                 SELECT wf.archivo_url FROM rsu_workflow_file wf
                 WHERE wf.proyecto_id=rp.proyecto_id AND wf.persona_firmante_id=p.persona_id
                   AND wf.proposito IN ('firma_responsable_actividad','firma_corresponsable_actividad','firma_asesor_actividad')
                 ORDER BY wf.fecha_subida DESC LIMIT 1
               ),ap.firma_url,'') AS firma_url
        FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        INNER JOIN persona p ON p.persona_id=rp.persona_id
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN departamento_academico dd ON dd.departamento_academico_id=d.departamento_academico_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN departamento_academico de ON de.departamento_academico_id=e.departamento_academico_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        LEFT JOIN departamento_academico da ON da.departamento_academico_id=af.departamento_academico_id
        LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
        WHERE rp.proyecto_id=%s AND rp.activo=1
        ORDER BY CASE LOWER(cr.nombre) WHEN 'responsable' THEN 1 WHEN 'corresponsable' THEN 2
                 WHEN 'docente_asesor' THEN 3 ELSE 4 END, p.apellidos,p.nombres
        """,
        (project_id,),
    )


def _responsible(project_id: str) -> dict[str, Any]:
    return next((row for row in _team(project_id) if str(row.get("rol") or "").lower() == "responsable"), {})


def _validate_team(responsible_id: str, members: list[ActivityMemberIn]) -> str:
    leadership = _person_type(responsible_id)
    if leadership not in {"docente", "estudiante"}:
        raise HTTPException(status_code=400, detail="La actividad específica debe estar liderada por un docente o estudiante.")
    ids = [responsible_id] + [item.persona_id for item in members]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail="Una persona no puede ocupar más de un rol en la misma actividad.")
    counts = {"corresponsable": 0, "colaborador": 0, "docente_asesor": 0}
    for item in members:
        counts[item.rol] += 1
        person_type = _person_type(item.persona_id)
        if item.rol in {"corresponsable", "docente_asesor"} and person_type != "docente":
            raise HTTPException(status_code=400, detail=f"El rol {item.rol.replace('_',' ')} solo puede asignarse a docentes.")
        if item.rol == "colaborador" and person_type != "estudiante":
            raise HTTPException(status_code=400, detail="En actividades específicas los colaboradores deben ser estudiantes.")
    if leadership == "docente":
        if counts["corresponsable"] > 8:
            raise HTTPException(status_code=400, detail="Una actividad liderada por docente admite hasta 8 docentes corresponsables.")
        if counts["colaborador"] > 40:
            raise HTTPException(status_code=400, detail="Una actividad liderada por docente admite hasta 40 estudiantes colaboradores.")
        if counts["docente_asesor"]:
            raise HTTPException(status_code=400, detail="El docente asesor solo corresponde a actividades lideradas por estudiantes.")
    else:
        if counts["corresponsable"]:
            raise HTTPException(status_code=400, detail="Una actividad liderada por estudiante no admite corresponsables.")
        if counts["colaborador"] > 15:
            raise HTTPException(status_code=400, detail="Una actividad liderada por estudiante admite hasta 15 colaboradores.")
        if counts["docente_asesor"] != 1:
            raise HTTPException(status_code=400, detail="Una actividad liderada por estudiante debe incluir exactamente 1 docente asesor.")
    return leadership


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
        """SELECT * FROM rsu_annex_submission
           WHERE proyecto_id=%s AND anexo_numero=%s AND es_actual=1
           ORDER BY revision DESC LIMIT 1""",
        (project_id, int(annex)),
    )
    if row and row.get("contenido_json"):
        try:
            row["contenido"] = json.loads(row["contenido_json"])
        except Exception:
            row["contenido"] = {}
    return row


def _insert_submission(cur, project_id: str, annex: int, person_id: str, content: dict[str, Any], revision: int) -> str:
    submission_id = str(uuid4())
    cur.execute(
        "UPDATE rsu_annex_submission SET es_actual=0 WHERE proyecto_id=%s AND anexo_numero=%s",
        (project_id, int(annex)),
    )
    cur.execute(
        """INSERT INTO rsu_annex_submission
           (submission_id,proyecto_id,anexo_numero,revision,presentado_por,contenido_json,estado,es_actual)
           VALUES (%s,%s,%s,%s,%s,%s,'presentado',1)""",
        (submission_id, project_id, int(annex), int(revision), person_id, json.dumps(content, ensure_ascii=False, default=str)),
    )
    return submission_id


def _reviewers(project_id: str) -> dict[str, dict[str, Any]]:
    responsible = _responsible(project_id)
    faculty = str(responsible.get("facultad") or "").strip()
    return {
        "director_rsu": _authority("director_rsu") or {},
        "director_facultad": _authority("director_facultad", faculty) or {},
        "vicepresidente": _authority("vicepresidente_academico") or {},
    }


def _validate_review_authorities(project_id: str) -> dict[str, dict[str, Any]]:
    roles = _reviewers(project_id)
    if not roles["director_rsu"]:
        raise HTTPException(status_code=400, detail="Registra una Directora o Director de RSU activo.")
    if not roles["director_facultad"]:
        raise HTTPException(status_code=400, detail="Registra un Director de Facultad activo para la facultad del responsable.")
    return roles


def _validate_authorities(project_id: str) -> dict[str, dict[str, Any]]:
    roles = _validate_review_authorities(project_id)
    if not roles["vicepresidente"]:
        raise HTTPException(status_code=400, detail="Registra un Vicepresidente académico activo.")
    if not roles["vicepresidente"].get("usuario_id"):
        raise HTTPException(
            status_code=400,
            detail="El Vicepresidente académico debe tener una cuenta de usuario activa para recibir el memorándum.",
        )
    return roles


def _assign_reviewers(cur, project_id: str, annex: int, revision: int) -> None:
    roles = _validate_review_authorities(project_id)
    faculty = str(_responsible(project_id).get("facultad") or "")
    for role, reviewer_id in [
        ("administrador_rsu", None),
        ("director_facultad", roles["director_facultad"].get("persona_id")),
    ]:
        cur.execute(
            """INSERT INTO rsu_project_review_assignment
               (assignment_id,proyecto_id,anexo_numero,revision,rol_revisor,revisor_persona_id,facultad,estado,activo)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'pendiente',1)
               ON DUPLICATE KEY UPDATE revisor_persona_id=VALUES(revisor_persona_id),facultad=VALUES(facultad),estado='pendiente',activo=1,evaluation_id=NULL,fecha_revision=NULL""",
            (str(uuid4()), project_id, int(annex), int(revision), role, reviewer_id, faculty),
        )


def _user_role(user: AuthUser) -> str:
    return str(user.rol_sistema or "").lower()


def _assert_activity_write_access(user: AuthUser) -> None:
    if _user_role(user) == "vicepresidente_academico":
        raise HTTPException(
            status_code=403,
            detail="El perfil del Vicepresidente académico dispone únicamente de acceso de consulta a las actividades específicas.",
        )


def _can_access(project_id: str, user: AuthUser) -> bool:
    # Las autoridades globales de RSU y el Vicepresidente Académico pueden
    # consultar cualquier actividad específica. El acceso del Vicepresidente
    # es estrictamente de lectura; las operaciones de edición/evaluación
    # continúan protegidas por sus validaciones específicas.
    if _user_role(user) in {"administrador_rsu", "director_rsu", "vicepresidente_academico"}:
        return True
    membership = fetch_one("SELECT 1 AS ok FROM rol_proyecto WHERE proyecto_id=%s AND persona_id=%s AND activo=1 LIMIT 1", (project_id, user.persona_id))
    if membership:
        return True
    assignment = fetch_one(
        "SELECT 1 AS ok FROM rsu_project_review_assignment WHERE proyecto_id=%s AND revisor_persona_id=%s LIMIT 1",
        (project_id, user.persona_id),
    )
    return bool(assignment)


def _assert_access(project_id: str, user: AuthUser) -> dict[str, Any]:
    row = fetch_one(
        """
        SELECT p.*,la.nombre AS linea_accion,pa.nombre AS periodo_academico,tp.nombre AS tipo_proyecto,
               w.estado_flujo,w.etapa_actual,w.revision_inicial,w.revision_final,w.fecha_limite_subsanacion,
               w.fecha_inicio_ejecucion,w.fecha_aprobacion_inicial,w.fecha_aprobacion_final,w.fecha_certificacion
        FROM proyecto p
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=p.periodo_academico_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN rsu_specific_activity_workflow w ON w.proyecto_id=p.proyecto_id
        WHERE p.proyecto_id=%s AND LOWER(tp.nombre) LIKE '%%actividades espec%%' LIMIT 1
        """,
        (project_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Actividad específica no encontrada.")
    _expire_if_needed(row)
    if not _can_access(project_id, user):
        raise HTTPException(status_code=403, detail="No tienes acceso a esta actividad específica.")
    return row


def _expire_if_needed(activity: dict[str, Any]) -> None:
    state = str(activity.get("estado_flujo") or "")
    deadline = activity.get("fecha_limite_subsanacion")
    if state not in {"actividad_observada", "actividad_cierre_observado"} or not deadline:
        return
    if isinstance(deadline, str):
        deadline = date.fromisoformat(deadline[:10])
    if date.today() <= deadline:
        return
    project_id = str(activity["proyecto_id"])
    execute(
        """UPDATE rsu_specific_activity_workflow
           SET estado_flujo='actividad_no_presentada',etapa_actual='finalizada',motivo_fin='Plazo de subsanación vencido'
           WHERE proyecto_id=%s""",
        (project_id,),
    )
    execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id("actividad_no_presentada"), project_id))
    activity["estado_flujo"] = "actividad_no_presentada"
    activity["etapa_actual"] = "finalizada"


def _assignment_for_user(project_id: str, annex: int, user: AuthUser) -> dict[str, Any] | None:
    """Devuelve únicamente una asignación de revisión todavía pendiente.

    Las asignaciones aprobadas u observadas se conservan activas para mantener
    la trazabilidad y el acceso histórico al expediente, pero no deben volver a
    habilitar la pantalla de evaluación para el mismo revisor.
    """
    exact = fetch_one(
        """SELECT * FROM rsu_project_review_assignment
           WHERE proyecto_id=%s AND anexo_numero=%s AND revisor_persona_id=%s
             AND activo=1 AND estado='pendiente'
           ORDER BY revision DESC LIMIT 1""",
        (project_id, int(annex), user.persona_id),
    )
    if exact:
        return exact
    if _user_role(user) not in {"administrador_rsu", "director_rsu"}:
        return None
    return fetch_one(
        """SELECT * FROM rsu_project_review_assignment
           WHERE proyecto_id=%s AND anexo_numero=%s AND rol_revisor='administrador_rsu'
             AND activo=1 AND estado='pendiente' AND revisor_persona_id IS NULL
           ORDER BY revision DESC LIMIT 1""",
        (project_id, int(annex)),
    )


def _activity_payload(project_id: str, user: AuthUser | None = None) -> dict[str, Any]:
    activity = fetch_one(
        """
        SELECT p.proyecto_id,p.titulo,p.tipo_liderazgo,p.lugar_ejecucion,p.fecha_inicio,p.fecha_fin,
               p.fecha_presentacion,la.linea_accion_id,la.nombre AS linea_accion,pa.nombre AS periodo_academico,
               tp.nombre AS tipo_proyecto,w.*
        FROM proyecto p
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=p.periodo_academico_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        INNER JOIN rsu_specific_activity_workflow w ON w.proyecto_id=p.proyecto_id
        WHERE p.proyecto_id=%s LIMIT 1
        """,
        (project_id,),
    )
    if not activity:
        raise HTTPException(status_code=404, detail="Actividad específica no encontrada.")
    _expire_if_needed(activity)
    activity["equipo"] = _team(project_id)
    activity["firmas"] = _activity_signatures(project_id)
    for annex in (1, 8, 10):
        sub = _current_submission(project_id, annex)
        activity[f"anexo_{annex}"] = dict((sub or {}).get("contenido") or {})
        activity[f"anexo_{annex}_revision"] = int((sub or {}).get("revision") or 0)
    evaluations = fetch_all(
        """SELECT e.*,CONCAT(p.nombres,' ',p.apellidos) AS evaluador
           FROM rsu_annex_evaluation e INNER JOIN persona p ON p.persona_id=e.evaluador_persona_id
           WHERE e.proyecto_id=%s AND e.anexo_numero IN (9,10)
           ORDER BY e.anexo_numero,e.revision,e.fecha_evaluacion""",
        (project_id,),
    )
    for row in evaluations:
        try:
            row["criterios"] = json.loads(row.get("criterios_json") or "[]")
        except Exception:
            row["criterios"] = []
    activity["evaluaciones"] = evaluations
    assignments = fetch_all(
        """SELECT a.*,CONCAT(p.nombres,' ',p.apellidos) AS revisor
           FROM rsu_project_review_assignment a LEFT JOIN persona p ON p.persona_id=a.revisor_persona_id
           WHERE a.proyecto_id=%s AND a.anexo_numero IN (9,10)
           ORDER BY a.anexo_numero,a.revision,a.rol_revisor""",
        (project_id,),
    )
    activity["asignaciones"] = assignments
    activity["memorandums"] = fetch_all(
        "SELECT * FROM rsu_specific_activity_memorandum WHERE proyecto_id=%s ORDER BY fecha_registro",
        (project_id,),
    )
    activity["certificados"] = fetch_all(
        """SELECT c.certificado_id,c.persona_id,c.horas_certificadas,c.fecha_emision,c.archivo_url,
                  CONCAT(p.nombres,' ',p.apellidos) AS participante
           FROM certificado c INNER JOIN persona p ON p.persona_id=c.persona_id
           WHERE c.proyecto_id=%s ORDER BY p.apellidos,p.nombres""",
        (project_id,),
    )
    if user:
        pending_initial = _assignment_for_user(project_id, 9, user)
        pending_final = _assignment_for_user(project_id, 10, user)
        historical_initial = fetch_one(
            """SELECT estado FROM rsu_project_review_assignment
               WHERE proyecto_id=%s AND anexo_numero=9 AND revisor_persona_id=%s
               ORDER BY revision DESC,fecha_revision DESC LIMIT 1""",
            (project_id, user.persona_id),
        )
        historical_final = fetch_one(
            """SELECT estado FROM rsu_project_review_assignment
               WHERE proyecto_id=%s AND anexo_numero=10 AND revisor_persona_id=%s
               ORDER BY revision DESC,fecha_revision DESC LIMIT 1""",
            (project_id, user.persona_id),
        )
        role = _user_role(user)
        is_vicepresident = role == "vicepresidente_academico"
        activity["permisos"] = {
            "es_responsable": any(str(x.get("persona_id")) == user.persona_id and str(x.get("rol")).lower() == "responsable" for x in activity["equipo"]),
            "es_admin": role in {"administrador_rsu", "director_rsu"},
            "es_director_facultad": role == "director_facultad",
            "es_vicepresidente": is_vicepresident,
            "solo_lectura": is_vicepresident,
            # El Vicepresidente nunca recibe asignaciones de evaluación.
            "puede_evaluar_inicial": bool(pending_initial) and not is_vicepresident,
            "puede_evaluar_final": bool(pending_final) and not is_vicepresident,
            "estado_revision_inicial_usuario": (historical_initial or {}).get("estado"),
            "estado_revision_final_usuario": (historical_final or {}).get("estado"),
        }
    return activity


def _pdf_path(project_id: str, number: int, revision: int) -> Path:
    folder = _settings_root() / project_id / "pdf"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"anexo_{number}_revision_{revision}.pdf"


def _annex_pdf_data(project_id: str, number: int) -> dict[str, Any]:
    payload = _activity_payload(project_id)
    if number == 9:
        evals = [e for e in payload["evaluaciones"] if int(e.get("anexo_numero") or 0) == 9]
        latest_revision = max([int(e.get("revision") or 0) for e in evals] or [1])
        latest = [e for e in evals if int(e.get("revision") or 0) == latest_revision]
        merged: dict[str, Any] = {"criterios": [], "observaciones": "", "revisores": []}
        if latest:
            criteria_map: dict[str, bool] = {}
            observations: list[str] = []
            for evaluation in latest:
                for criterion in evaluation.get("criterios") or []:
                    key = str(criterion.get("clave") or "")
                    criteria_map[key] = bool(criteria_map.get(key, True) and bool(criterion.get("cumple"))) if key in criteria_map else bool(criterion.get("cumple"))
                if evaluation.get("observaciones"):
                    observations.append(f"{evaluation.get('evaluador')}: {evaluation.get('observaciones')}")
                merged["revisores"].append({
                    "nombre_completo": evaluation.get("evaluador"),
                    "cargo": "Administrador RSU" if evaluation.get("rol_revisor") == "administrador_rsu" else "Director de Facultad",
                    "firma_url": ((
                        _authority("director_rsu") or {}
                    ) if evaluation.get("rol_revisor") == "administrador_rsu" else (
                        _authority("director_facultad", _responsible(project_id).get("facultad")) or {}
                    )).get("firma_url"),
                })
            merged["criterios"] = [{"clave": key, "cumple": value} for key, value in criteria_map.items()]
            merged["observaciones"] = "\n".join(observations)
        payload["evaluacion"] = merged
    return payload


def _memo_number(kind: str) -> str:
    prefix = "AE-APR" if kind == "aprobacion" else "AE-FIN"
    row = fetch_one("SELECT COUNT(*) AS total FROM rsu_specific_activity_memorandum WHERE YEAR(fecha_emision)=YEAR(CURDATE())") or {}
    return f"MEMORÁNDUM {prefix}-{int(row.get('total') or 0)+1:04d}-{date.today().year}-UNAB-VP.ACAD/DRS"


def _issue_memorandum(project_id: str, kind: str, emitter: AuthUser) -> dict[str, Any]:
    # Evita que dos clics o reintentos generen el mismo memorándum en paralelo.
    # La segunda solicitud espera a la primera y reutiliza el registro ya creado.
    with _memorandum_lock(project_id, kind):
        current = fetch_one(
            "SELECT * FROM rsu_specific_activity_memorandum WHERE proyecto_id=%s AND tipo=%s LIMIT 1",
            (project_id, kind),
        )
        if current:
            return current

        activity = _activity_payload(project_id)
        roles = _validate_authorities(project_id)
        responsible = _responsible(project_id)
        template = Path(__file__).resolve().parents[4] / "official_annex_templates" / "Memorandum_087-2026_UNAB.docx"
        if not template.exists():
            raise HTTPException(status_code=500, detail="No se encontró la plantilla del memorándum.")
        output_dir = _settings_root() / project_id / "memorandums"
        number = _memo_number(kind)
        project_data = {
            "titulo": activity["titulo"],
            "tipo_proyecto": "Actividad Específica y de Proyección Social",
            "periodo_academico": activity["periodo_academico"],
        }
        if kind == "aprobacion":
            docx, pdf = generate_memorandum(
                template_path=template, output_dir=output_dir, number=number, issue_date=date.today(),
                project=project_data, responsible=responsible, vice_president=roles["vicepresidente"],
                director=roles["director_rsu"], director_signature_path=roles["director_rsu"].get("firma_url"),
            )
        else:
            docx, pdf = generate_certificate_memorandum(
                template_path=template, output_dir=output_dir, number=number, issue_date=date.today(),
                project=project_data, responsible=responsible, vice_president=roles["vicepresidente"],
                director=roles["director_rsu"], director_signature_path=roles["director_rsu"].get("firma_url"),
                certificate_count=0, purpose="final_approval",
            )
        if not pdf or not Path(pdf).exists() or Path(pdf).stat().st_size <= 0:
            raise HTTPException(status_code=500, detail="No se pudo generar el PDF del memorándum institucional.")

        memo_id = str(uuid4())
        execute(
            """INSERT INTO rsu_specific_activity_memorandum
               (memorandum_id,proyecto_id,tipo,numero_memorandum,fecha_emision,archivo_docx_url,archivo_pdf_url,
                responsable_persona_id,vicepresidente_persona_id,directora_persona_id,emitido_por)
               VALUES (%s,%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,%s)""",
            (
                memo_id, project_id, kind, number, str(docx), str(pdf), responsible["persona_id"],
                roles["vicepresidente"].get("persona_id"), roles["director_rsu"].get("persona_id"), emitter.persona_id,
            ),
        )
        field = "fecha_memorandum_aprobacion" if kind == "aprobacion" else "fecha_memorandum_final"
        execute(f"UPDATE rsu_specific_activity_workflow SET {field}=NOW() WHERE proyecto_id=%s", (project_id,))
        return fetch_one("SELECT * FROM rsu_specific_activity_memorandum WHERE memorandum_id=%s", (memo_id,)) or {}


async def _notify_memorandum(project_id: str, kind: str, memo: dict[str, Any], emitter: AuthUser) -> None:
    roles = _reviewers(project_id)
    recipients = project_user_ids(project_id, roles=["responsable"])
    vice_user_id = str(roles["vicepresidente"].get("usuario_id") or "").strip()
    if vice_user_id:
        recipients.append(vice_user_id)
    recipients = sorted({str(user_id) for user_id in recipients if str(user_id).strip()})
    label = "Memorándum de aprobación" if kind == "aprobacion" else "Memorándum final"
    await notify_users(
        recipients,
        notification_type=f"actividad_{kind}_memorandum",
        title=f"{label} emitido",
        message=f"El {label.lower()} de la actividad específica ya está disponible en el expediente.",
        module="Actividades específicas",
        priority="alta",
        entity_type="memorandum",
        entity_id=str(memo.get("memorandum_id") or ""),
        project_id=project_id,
        data={"proyecto_id": project_id, "pantalla": "actividad_inicio" if kind == "aprobacion" else "actividad_certificacion", "modalidad": "actividad_especifica"},
        event_key=f"actividad:{project_id}:memorandum:{kind}",
        generated_by=emitter.nombre_completo,
        important=True,
    )


@router.get("/convocatoria-vigente")
def current_annual_call(user: AuthUser = Depends(get_current_user)):
    year = date.today().year
    call_id = ensure_annual_specific_activity_call(year)
    row = fetch_one(
        """SELECT c.convocatoria_id,c.fecha_inicio,c.fecha_limite_presentacion,ce.nombre AS estado,
                  pa.nombre AS periodo,tp.nombre AS tipo
           FROM convocatoria c INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=c.estado_id
           INNER JOIN periodo_academico pa ON pa.periodo_academico_id=c.periodo_academico_id
           INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=c.tipo_proyecto_id
           WHERE c.convocatoria_id=%s""",
        (call_id,),
    )
    return row


@router.get("/catalogo/criterios")
def criteria_catalog(user: AuthUser = Depends(get_current_user)):
    return {
        "inicial": [{"clave": key, "nombre": name} for key, name in INITIAL_CRITERIA],
        "final": [{"clave": key, "nombre": name} for key, name in FINAL_CRITERIA],
    }


@router.get("")
def list_activities(user: AuthUser = Depends(get_current_user)):
    params: list[Any] = []
    scope = "1=1"
    role = _user_role(user)
    if role in {"administrador_rsu", "director_rsu", "vicepresidente_academico"}:
        # El Vicepresidente Académico visualiza el universo completo de
        # actividades, igual que las autoridades globales, pero en modo lectura.
        pass
    elif role == "director_facultad":
        scope = "(LOWER(TRIM(COALESCE(rf.facultad,'')))=LOWER(TRIM(%s)) OR EXISTS (SELECT 1 FROM rsu_project_review_assignment ra WHERE ra.proyecto_id=p.proyecto_id AND ra.revisor_persona_id=%s))"
        params.extend([user.facultad_asignada or "", user.persona_id])
    else:
        scope = "EXISTS (SELECT 1 FROM rol_proyecto rm WHERE rm.proyecto_id=p.proyecto_id AND rm.persona_id=%s AND rm.activo=1)"
        params.append(user.persona_id)
    rows = fetch_all(
        f"""
        SELECT p.proyecto_id,p.titulo,la.nombre AS linea_accion,pa.nombre AS periodo_academico,w.estado_flujo,w.etapa_actual,
               p.fecha_inicio,p.fecha_fin,w.fecha_limite_subsanacion,
               CONCAT(pr.nombres,' ',pr.apellidos) AS responsable,rf.facultad
        FROM proyecto p
        INNER JOIN rsu_specific_activity_workflow w ON w.proyecto_id=p.proyecto_id
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
        WHERE {scope}
        ORDER BY w.actualizado_en DESC,p.fecha_creacion DESC
        """,
        tuple(params),
    )
    for row in rows:
        _expire_if_needed(row)
        project_id = str(row.get("proyecto_id") or "")
        row["puede_evaluar_inicial"] = bool(_assignment_for_user(project_id, 9, user))
        row["puede_evaluar_final"] = bool(_assignment_for_user(project_id, 10, user))
    return rows


@router.post("")
async def create_activity(data: ActivityCreateIn, user: AuthUser = Depends(get_current_user)):
    _assert_activity_write_access(user)
    leadership = _validate_team(user.persona_id, data.miembros)
    required_signers = {str(user.persona_id)} | {
        str(item.persona_id) for item in data.miembros if item.rol in {"corresponsable", "docente_asesor"}
    }
    supplied_signers = {str(item.person_id or user.persona_id) for item in data.firmas}
    missing_signers = required_signers - supplied_signers
    if missing_signers:
        raise HTTPException(status_code=400, detail="Adjunta la firma del responsable y de cada corresponsable o docente asesor seleccionado.")
    call_id = ensure_annual_specific_activity_call(date.today().year)
    call = fetch_one("SELECT * FROM convocatoria WHERE convocatoria_id=%s", (call_id,))
    if not call or not (call["fecha_inicio"] <= date.today() <= call["fecha_limite_presentacion"]):
        raise HTTPException(status_code=400, detail="La convocatoria anual de actividades específicas no está vigente.")
    _validate_authorities_for_responsible = _authority("director_facultad", _person_faculty(user.persona_id))
    if not _validate_authorities_for_responsible:
        raise HTTPException(status_code=400, detail="La facultad del responsable debe tener un Director de Facultad activo.")
    period_id = str(call["periodo_academico_id"])
    project_id = str(uuid4())
    annex1 = {
        "domicilio": data.domicilio or user.domicilio or "",
        "condicion": data.condicion or user.condicion or leadership.title(),
    }
    annex8 = {
        "lugar_entorno": data.lugar_entorno,
        "resumen": data.resumen,
        "problematica": data.problematica,
        "objetivo": data.objetivo,
        "metodologia": data.metodologia,
        "impacto_social": data.impacto_social,
        "medio_difusion": data.medio_difusion,
        "financiamiento": data.financiamiento,
    }
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO proyecto
                   (proyecto_id,convocatoria_id,periodo_academico_id,linea_accion_id,titulo,tipo_liderazgo,
                    financiado,estado_id,lugar_ejecucion,poblacion_objetivo,resumen,fecha_inicio,fecha_fin,fecha_presentacion)
                   VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s,NOW())""",
                (
                    project_id, call_id, period_id, data.linea_accion_id, data.titulo, leadership,
                    _state_id("actividad_en_evaluacion"), data.lugar_entorno, data.lugar_entorno,
                    data.resumen, data.fecha_inicio, data.fecha_fin,
                ),
            )
            cur.execute(
                """INSERT INTO rsu_specific_activity_workflow
                   (proyecto_id,estado_flujo,etapa_actual,revision_inicial,revision_final,horas_totales)
                   VALUES (%s,'actividad_en_evaluacion','evaluacion_inicial',1,1,%s)""",
                (project_id, data.horas_totales),
            )
            cur.execute(
                """INSERT INTO rol_proyecto
                   (rol_proyecto_id,persona_id,proyecto_id,rol_id,horas_semanales,horas_acumuladas,fecha_asignacion,activo)
                   VALUES (%s,%s,%s,%s,NULL,0,CURDATE(),1)""",
                (str(uuid4()), user.persona_id, project_id, _role_id("responsable")),
            )
            for member in data.miembros:
                cur.execute(
                    """INSERT INTO rol_proyecto
                       (rol_proyecto_id,persona_id,proyecto_id,rol_id,horas_semanales,horas_acumuladas,fecha_asignacion,activo)
                       VALUES (%s,%s,%s,%s,NULL,0,CURDATE(),1)""",
                    (str(uuid4()), member.persona_id, project_id, _role_id(member.rol)),
                )
            _store_activity_signatures(cur, project_id, data.firmas, user.persona_id)
            _insert_submission(cur, project_id, 1, user.persona_id, annex1, 1)
            _insert_submission(cur, project_id, 8, user.persona_id, annex8, 1)
            _assign_reviewers(cur, project_id, 9, 1)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    await notify_admins(
        notification_type="actividad_plan_presentado", title="Plan de actividad presentado",
        message=f"Se presentaron los Anexos 1 y 8 de la actividad «{data.titulo}».", module="Actividades específicas",
        priority="alta", entity_type="actividad_especifica", entity_id=project_id, project_id=project_id,
        annex_number=8, data={"proyecto_id": project_id, "pantalla": "actividad_evaluacion_inicial", "modalidad": "actividad_especifica"},
        event_key=f"actividad:{project_id}:plan:revision:1", generated_by=user.nombre_completo, important=True,
    )
    director = _authority("director_facultad", _person_faculty(user.persona_id))
    if director and director.get("usuario_id"):
        await notify_users(
            [str(director["usuario_id"])], notification_type="actividad_plan_presentado",
            title="Plan de actividad presentado", message=f"La actividad «{data.titulo}» requiere su evaluación mediante el Anexo 9.",
            module="Actividades específicas", priority="alta", entity_type="actividad_especifica", entity_id=project_id,
            project_id=project_id, annex_number=9, data={"proyecto_id": project_id, "pantalla": "actividad_evaluacion_inicial", "modalidad": "actividad_especifica"},
            event_key=f"actividad:{project_id}:director:revision:1", generated_by=user.nombre_completo, important=True,
        )
    return _activity_payload(project_id, user)


@router.get("/{project_id}")
def detail(project_id: str, user: AuthUser = Depends(get_current_user)):
    _assert_access(project_id, user)
    return _activity_payload(project_id, user)


@router.put("/{project_id}/subsanar-plan")
async def correct_plan(project_id: str, data: ActivityCreateIn, user: AuthUser = Depends(get_current_user)):
    _assert_activity_write_access(user)
    activity = _assert_access(project_id, user)
    if not any(str(x.get("persona_id")) == user.persona_id and str(x.get("rol")).lower() == "responsable" for x in _team(project_id)):
        raise HTTPException(status_code=403, detail="Solo el responsable puede subsanar el plan.")
    if str(activity.get("estado_flujo")) != "actividad_observada":
        raise HTTPException(status_code=400, detail="La actividad no tiene observaciones iniciales pendientes.")
    deadline = activity.get("fecha_limite_subsanacion")
    if deadline and date.today() > (deadline if isinstance(deadline, date) else date.fromisoformat(str(deadline)[:10])):
        _expire_if_needed(activity)
        raise HTTPException(status_code=400, detail="Venció el plazo de tres días hábiles; la actividad se considera no presentada.")
    leadership = _validate_team(user.persona_id, data.miembros)
    required_signers = {str(user.persona_id)} | {
        str(item.persona_id) for item in data.miembros if item.rol in {"corresponsable", "docente_asesor"}
    }
    existing_signers = {str(item.get("persona_firmante_id") or "") for item in _activity_signatures(project_id)}
    supplied_signers = {str(item.person_id or user.persona_id) for item in data.firmas}
    if required_signers - (existing_signers | supplied_signers):
        raise HTTPException(status_code=400, detail="Adjunta la firma del responsable y de cada corresponsable o docente asesor seleccionado.")
    revision = int(activity.get("revision_inicial") or 1) + 1
    annex1 = {"domicilio": data.domicilio or user.domicilio or "", "condicion": data.condicion or user.condicion or leadership.title()}
    annex8 = {key: getattr(data, key) for key in ("lugar_entorno", "resumen", "problematica", "objetivo", "metodologia", "impacto_social", "medio_difusion", "financiamiento")}
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM rol_proyecto WHERE proyecto_id=%s AND persona_id<>%s", (project_id, user.persona_id))
            for member in data.miembros:
                cur.execute(
                    """INSERT INTO rol_proyecto
                       (rol_proyecto_id,persona_id,proyecto_id,rol_id,horas_semanales,horas_acumuladas,fecha_asignacion,activo)
                       VALUES (%s,%s,%s,%s,NULL,0,CURDATE(),1)""",
                    (str(uuid4()), member.persona_id, project_id, _role_id(member.rol)),
                )
            if data.firmas:
                _store_activity_signatures(cur, project_id, data.firmas, user.persona_id)
            cur.execute(
                """UPDATE proyecto SET linea_accion_id=%s,titulo=%s,tipo_liderazgo=%s,lugar_ejecucion=%s,
                   poblacion_objetivo=%s,resumen=%s,fecha_inicio=%s,fecha_fin=%s,estado_id=%s WHERE proyecto_id=%s""",
                (data.linea_accion_id, data.titulo, leadership, data.lugar_entorno, data.lugar_entorno, data.resumen, data.fecha_inicio, data.fecha_fin, _state_id("actividad_en_evaluacion"), project_id),
            )
            _insert_submission(cur, project_id, 1, user.persona_id, annex1, revision)
            _insert_submission(cur, project_id, 8, user.persona_id, annex8, revision)
            cur.execute(
                """UPDATE rsu_specific_activity_workflow
                   SET estado_flujo='actividad_en_evaluacion',etapa_actual='evaluacion_inicial',revision_inicial=%s,
                       horas_totales=%s,fecha_limite_subsanacion=NULL
                   WHERE proyecto_id=%s""",
                (revision, data.horas_totales, project_id),
            )
            cur.execute("UPDATE rsu_project_review_assignment SET activo=0 WHERE proyecto_id=%s AND anexo_numero=9", (project_id,))
            _assign_reviewers(cur, project_id, 9, revision)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    await notify_admins(
        notification_type="actividad_plan_subsanado", title="Plan de actividad subsanado",
        message=f"El responsable corrigió los Anexos 1 y 8 de «{data.titulo}».", module="Actividades específicas",
        priority="alta", entity_type="actividad_especifica", entity_id=project_id, project_id=project_id, annex_number=8,
        data={"proyecto_id": project_id, "pantalla": "actividad_evaluacion_inicial", "modalidad": "actividad_especifica"}, event_key=f"actividad:{project_id}:plan:revision:{revision}",
        generated_by=user.nombre_completo, important=True,
    )
    return _activity_payload(project_id, user)


async def _evaluate(project_id: str, annex: int, data: ActivityEvaluationIn, reviewer: AuthUser) -> dict[str, Any]:
    activity = _assert_access(project_id, reviewer)
    assignment = _assignment_for_user(project_id, annex, reviewer)
    if not assignment:
        raise HTTPException(status_code=403, detail="No tienes una revisión pendiente para esta actividad.")
    expected = {key for key, _ in (INITIAL_CRITERIA if annex == 9 else FINAL_CRITERIA)}
    supplied = {item.clave for item in data.criterios}
    if expected != supplied:
        raise HTTPException(status_code=400, detail="Debe evaluar todos los criterios del formulario.")
    failed = [item for item in data.criterios if not item.cumple]
    if failed and not str(data.observaciones or "").strip() and not any(str(item.observacion or "").strip() for item in failed):
        raise HTTPException(status_code=400, detail="Registra las observaciones que debe subsanar el responsable.")
    revision = int(assignment.get("revision") or 1)
    role = str(assignment.get("rol_revisor") or "")
    evaluation_id = str(uuid4())
    total_yes = len(data.criterios) - len(failed)
    total_no = len(failed)
    result = "aprobado" if total_no == 0 else "observado"
    deadline = None if result == "aprobado" else _add_business_days(date.today(), 3)
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            if role == "administrador_rsu" and not assignment.get("revisor_persona_id"):
                cur.execute("UPDATE rsu_project_review_assignment SET revisor_persona_id=%s WHERE assignment_id=%s", (reviewer.persona_id, assignment["assignment_id"]))
            cur.execute(
                """INSERT INTO rsu_annex_evaluation
                   (evaluation_id,proyecto_id,anexo_numero,revision,evaluador_persona_id,criterios_json,total_si,total_no,
                    resultado,observaciones,fecha_limite_subsanacion,rol_revisor,facultad_revisor)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (evaluation_id, project_id, annex, revision, reviewer.persona_id,
                 json.dumps([item.model_dump() for item in data.criterios], ensure_ascii=False), total_yes, total_no,
                 result, data.observaciones, deadline, role, assignment.get("facultad")),
            )
            cur.execute(
                """UPDATE rsu_project_review_assignment SET estado=%s,evaluation_id=%s,fecha_revision=NOW(),revisor_persona_id=%s
                   WHERE assignment_id=%s""",
                (result, evaluation_id, reviewer.persona_id, assignment["assignment_id"]),
            )
            if result == "observado":
                flow_state = "actividad_observada" if annex == 9 else "actividad_cierre_observado"
                stage = "subsanacion_inicial" if annex == 9 else "subsanacion_final"
                cur.execute(
                    """UPDATE rsu_specific_activity_workflow SET estado_flujo=%s,etapa_actual=%s,fecha_limite_subsanacion=%s WHERE proyecto_id=%s""",
                    (flow_state, stage, deadline, project_id),
                )
                cur.execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id(flow_state), project_id))
                cur.execute("UPDATE rsu_project_review_assignment SET activo=0 WHERE proyecto_id=%s AND anexo_numero=%s AND revision=%s AND assignment_id<>%s", (project_id, annex, revision, assignment["assignment_id"]))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()

    if result == "observado":
        await notify_project_responsibles(
            project_id, notification_type="actividad_observada", title="Actividad observada",
            message=f"La actividad «{activity['titulo']}» tiene observaciones. Deben subsanarse hasta el {deadline.strftime('%d/%m/%Y')}.",
            module="Actividades específicas", priority="alta", entity_type="actividad_especifica", entity_id=project_id,
            annex_number=8 if annex == 9 else 10, data={"proyecto_id": project_id, "fecha_limite": deadline.isoformat(), "pantalla": "actividad_subsanacion", "modalidad": "actividad_especifica"},
            event_key=f"actividad:{project_id}:anexo:{annex}:revision:{revision}:observado:{role}", generated_by=reviewer.nombre_completo, important=True,
        )
        return _activity_payload(project_id, reviewer)

    pending = fetch_one(
        """SELECT COUNT(*) AS total FROM rsu_project_review_assignment
           WHERE proyecto_id=%s AND anexo_numero=%s AND revision=%s AND activo=1 AND estado='pendiente'""",
        (project_id, annex, revision),
    )
    if int((pending or {}).get("total") or 0) == 0:
        # Primero se genera y registra el memorándum. Solo después se cambia la
        # etapa del flujo; así nunca queda una actividad “habilitada” sin PDF.
        kind = "aprobacion" if annex == 9 else "final"
        memo = await run_in_threadpool(_issue_memorandum, project_id, kind, reviewer)
        with get_connection() as conn:
            cur = conn.cursor()
            try:
                if annex == 9:
                    cur.execute(
                        """UPDATE rsu_specific_activity_workflow SET estado_flujo='actividad_memorandum_emitido',etapa_actual='lista_ejecucion',
                           fecha_aprobacion_inicial=NOW(),fecha_limite_subsanacion=NULL WHERE proyecto_id=%s""",
                        (project_id,),
                    )
                    cur.execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id("actividad_memorandum_emitido"), project_id))
                else:
                    cur.execute(
                        """UPDATE rsu_specific_activity_workflow SET estado_flujo='actividad_certificacion',etapa_actual='certificacion',
                           fecha_aprobacion_final=NOW(),fecha_limite_subsanacion=NULL WHERE proyecto_id=%s""",
                        (project_id,),
                    )
                    cur.execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id("actividad_certificacion"), project_id))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finally:
                cur.close()
        await _notify_memorandum(project_id, kind, memo, reviewer)
    return _activity_payload(project_id, reviewer)


@router.post("/{project_id}/evaluar-plan")
async def evaluate_plan(project_id: str, data: ActivityEvaluationIn, reviewer: AuthUser = Depends(require_project_reviewer)):
    _assert_activity_write_access(reviewer)
    return await _evaluate(project_id, 9, data, reviewer)


@router.post("/{project_id}/iniciar-ejecucion")
async def start_execution(project_id: str, data: ActivityStartIn, user: AuthUser = Depends(get_current_user)):
    _assert_activity_write_access(user)
    activity = _assert_access(project_id, user)
    if not any(str(x.get("persona_id")) == user.persona_id and str(x.get("rol")).lower() == "responsable" for x in _team(project_id)):
        raise HTTPException(status_code=403, detail="Solo el responsable puede iniciar la ejecución.")
    if str(activity.get("estado_flujo")) != "actividad_memorandum_emitido":
        raise HTTPException(status_code=400, detail="La actividad aún no cuenta con el memorándum de aprobación.")
    start = data.fecha_inicio_real or date.today()
    execute(
        """UPDATE rsu_specific_activity_workflow SET estado_flujo='actividad_en_ejecucion',etapa_actual='ejecucion',fecha_inicio_ejecucion=%s WHERE proyecto_id=%s""",
        (start, project_id),
    )
    execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id("actividad_en_ejecucion"), project_id))
    await notify_admins(
        notification_type="actividad_ejecucion_iniciada", title="Ejecución iniciada",
        message=f"El responsable inició la ejecución de «{activity['titulo']}».", module="Actividades específicas",
        priority="informativa", entity_type="actividad_especifica", entity_id=project_id, project_id=project_id,
        data={"proyecto_id": project_id, "pantalla": "actividad_ejecucion", "modalidad": "actividad_especifica"}, event_key=f"actividad:{project_id}:ejecucion:{start}", generated_by=user.nombre_completo,
    )
    return _activity_payload(project_id, user)


@router.post("/{project_id}/anexo-10")
async def submit_closure(project_id: str, data: ActivityClosureIn, user: AuthUser = Depends(get_current_user)):
    _assert_activity_write_access(user)
    activity = _assert_access(project_id, user)
    if not any(str(x.get("persona_id")) == user.persona_id and str(x.get("rol")).lower() == "responsable" for x in _team(project_id)):
        raise HTTPException(status_code=403, detail="Solo el responsable puede presentar el Anexo 10.")
    state = str(activity.get("estado_flujo"))
    if state not in {"actividad_en_ejecucion", "actividad_cierre_observado"}:
        raise HTTPException(status_code=400, detail="La actividad no se encuentra en ejecución o subsanación final.")
    if state == "actividad_cierre_observado":
        deadline = activity.get("fecha_limite_subsanacion")
        if deadline and date.today() > (deadline if isinstance(deadline, date) else date.fromisoformat(str(deadline)[:10])):
            _expire_if_needed(activity)
            raise HTTPException(status_code=400, detail="Venció el plazo de subsanación; la actividad se considera no presentada.")
        revision = int(activity.get("revision_final") or 1) + 1
    else:
        revision = int(activity.get("revision_final") or 1)
    content = {
        "lugar_entorno": data.lugar_entorno, "resumen": data.resumen, "problematica": data.problematica,
        "objetivo": data.objetivo, "metodologia": data.metodologia, "impacto_social": data.impacto_social,
        "evidencias": data.evidencias_descripcion, "financiamiento": data.financiamiento,
    }
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("UPDATE proyecto SET fecha_inicio=%s,fecha_fin=%s,lugar_ejecucion=%s,resumen=%s,estado_id=%s WHERE proyecto_id=%s", (data.fecha_inicio, data.fecha_fin, data.lugar_entorno, data.resumen, _state_id("actividad_cierre_presentado"), project_id))
            _insert_submission(cur, project_id, 10, user.persona_id, content, revision)
            cur.execute(
                """UPDATE rsu_specific_activity_workflow SET estado_flujo='actividad_cierre_presentado',etapa_actual='evaluacion_final',
                   revision_final=%s,horas_totales=%s,fecha_presentacion_cierre=NOW(),fecha_limite_subsanacion=NULL WHERE proyecto_id=%s""",
                (revision, data.horas_totales, project_id),
            )
            cur.execute(
                "UPDATE rol_proyecto SET horas_acumuladas=%s WHERE proyecto_id=%s AND activo=1",
                (data.horas_totales, project_id),
            )
            cur.execute("UPDATE rsu_project_review_assignment SET activo=0 WHERE proyecto_id=%s AND anexo_numero=10", (project_id,))
            _assign_reviewers(cur, project_id, 10, revision)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    await notify_admins(
        notification_type="actividad_cierre_presentado", title="Informe final presentado",
        message=f"El responsable presentó el Anexo 10 de «{activity['titulo']}».", module="Actividades específicas",
        priority="alta", entity_type="actividad_especifica", entity_id=project_id, project_id=project_id, annex_number=10,
        data={"proyecto_id": project_id, "pantalla": "actividad_evaluacion_final", "modalidad": "actividad_especifica"}, event_key=f"actividad:{project_id}:anexo10:revision:{revision}", generated_by=user.nombre_completo, important=True,
    )
    return _activity_payload(project_id, user)


@router.post("/{project_id}/evaluar-cierre")
async def evaluate_closure(project_id: str, data: ActivityEvaluationIn, reviewer: AuthUser = Depends(require_project_reviewer)):
    _assert_activity_write_access(reviewer)
    return await _evaluate(project_id, 10, data, reviewer)


@router.post("/{project_id}/certificar")
async def certify(project_id: str, data: ActivityCertificateIn, admin: AuthUser = Depends(require_admin)):
    _assert_activity_write_access(admin)
    activity = _assert_access(project_id, admin)
    deny_certificate_for_simulation(project_id)
    state = str(activity.get("estado_flujo") or "")
    team = _team(project_id)
    existing = fetch_all("SELECT certificado_id,persona_id,archivo_url FROM certificado WHERE proyecto_id=%s AND fecha_emision IS NOT NULL", (project_id,))
    if state == "actividad_certificada" and len(existing) >= len(team):
        result = _activity_payload(project_id, admin)
        result["certificacion_ya_emitida"] = True
        return result
    if state != "actividad_certificacion":
        raise HTTPException(status_code=400, detail="La actividad aún no está lista para certificación.")
    roles = _validate_authorities(project_id)
    director = roles["director_rsu"]
    signature = Path(str(director.get("firma_url") or ""))
    if not signature.exists():
        raise HTTPException(status_code=400, detail="La Dirección de RSU debe tener una firma institucional registrada.")
    emitted = data.fecha_emision or date.today()
    hours = int(data.horas_certificadas or activity.get("horas_totales") or 16)
    base = _settings_root() / project_id / "certificados"
    base.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[Path, dict[str, Any]]] = []
    prepared: list[dict[str, Any]] = []
    for person in team:
        cert_id = str(uuid4())
        output = base / f"certificado_{person['persona_id']}.pdf"
        identification = f"Código: {person.get('codigo')}" if str(person.get("tipo_persona")).lower() == "estudiante" else f"DNI: {person.get('dni')}"
        jobs.append((output, {
            "nombre_completo": person["nombre_completo"], "identificacion": identification,
            "titulo": activity["titulo"], "tipo_proyecto": "Actividad Específica y de Proyección Social",
            "linea_accion": activity["linea_accion"], "horas": hours,
            "programa_academico": person.get("escuela") or "UNAB", "fecha_emision": emitted.strftime("%d/%m/%Y"),
            "firma_director_path": str(signature), "nombre_director": director.get("nombre_completo"),
        }))
        prepared.append({"certificado_id": cert_id, "persona": person, "output": output})
    generated = {p.resolve() for p in generate_certificate_pdfs_batch(jobs)}
    for item in prepared:
        if item["output"].resolve() not in generated or not item["output"].exists():
            raise HTTPException(status_code=500, detail=f"No se pudo generar el certificado de {item['persona']['nombre_completo']}.")
    state_emitted = fetch_one("SELECT catalogo_estado_id FROM catalogo_estado WHERE entidad='certificado' AND LOWER(nombre)='emitido' LIMIT 1")
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            for item in prepared:
                current = fetch_one("SELECT certificado_id FROM certificado WHERE proyecto_id=%s AND persona_id=%s LIMIT 1", (project_id, item["persona"]["persona_id"]))
                if current:
                    cur.execute("UPDATE certificado SET horas_certificadas=%s,archivo_url=%s,fecha_emision=%s,estado_id=%s WHERE certificado_id=%s", (hours, str(item["output"]), emitted, state_emitted["catalogo_estado_id"], current["certificado_id"]))
                    item["certificado_id"] = str(current["certificado_id"])
                else:
                    cur.execute(
                        """INSERT INTO certificado
                           (certificado_id,persona_id,proyecto_id,horas_certificadas,archivo_url,fecha_solicitud,fecha_emision,codigo_verificacion,es_convalidacion,estado_id)
                           VALUES (%s,%s,%s,%s,%s,CURDATE(),%s,%s,0,%s)""",
                        (item["certificado_id"], item["persona"]["persona_id"], project_id, hours, str(item["output"]), emitted, f"AE-{uuid4().hex[:12].upper()}", state_emitted["catalogo_estado_id"]),
                    )
            cur.execute(
                "UPDATE rol_proyecto SET horas_acumuladas=%s WHERE proyecto_id=%s AND activo=1",
                (hours, project_id),
            )
            cur.execute("UPDATE rsu_specific_activity_workflow SET estado_flujo='actividad_certificada',etapa_actual='finalizada',fecha_certificacion=NOW() WHERE proyecto_id=%s", (project_id,))
            cur.execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id("actividad_certificada"), project_id))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    for item in prepared:
        account = fetch_one("SELECT usuario_id FROM usuario WHERE persona_id=%s AND activo=1", (item["persona"]["persona_id"],))
        if account:
            await notify_users(
                [str(account["usuario_id"])], notification_type="actividad_certificado_disponible",
                title="Certificado disponible", message=f"Tu certificado de la actividad «{activity['titulo']}» está disponible.",
                module="Actividades específicas", priority="informativa", entity_type="certificado", entity_id=item["certificado_id"],
                project_id=project_id, data={"proyecto_id": project_id, "certificado_id": item["certificado_id"], "pantalla": "actividad_certificacion", "modalidad": "actividad_especifica"},
                event_key=f"actividad:{project_id}:certificado:{item['persona']['persona_id']}", generated_by=admin.nombre_completo, important=True,
            )
    return _activity_payload(project_id, admin)


@router.get("/{project_id}/anexos/{annex}/pdf")
def download_annex(project_id: str, annex: int, user: AuthUser = Depends(get_current_user)):
    _assert_access(project_id, user)
    if annex not in {1, 8, 9, 10}:
        raise HTTPException(status_code=404, detail="Anexo no disponible para actividades específicas.")
    if annex in {1, 8, 10}:
        submission = _current_submission(project_id, annex)
        if not submission:
            raise HTTPException(status_code=404, detail=f"El Anexo {annex} aún no ha sido presentado.")
        revision = int(submission.get("revision") or 1)
    else:
        row = fetch_one("SELECT COALESCE(MAX(revision),0) AS revision FROM rsu_annex_evaluation WHERE proyecto_id=%s AND anexo_numero=9", (project_id,))
        revision = int((row or {}).get("revision") or 0)
        if revision <= 0:
            raise HTTPException(status_code=404, detail="El Anexo 9 aún no ha sido emitido.")
    output = _pdf_path(project_id, annex, revision)
    if not output.exists() or output.stat().st_size <= 0:
        generate_activity_pdf(annex, output, _annex_pdf_data(project_id, annex))
    title = _slug(_activity_payload(project_id)["titulo"])
    return FileResponse(str(output), media_type="application/pdf", filename=f"{title}-anexo-{annex}.pdf")


@router.get("/{project_id}/memorandums/{kind}")
def download_memorandum(project_id: str, kind: Literal["aprobacion", "final"], user: AuthUser = Depends(get_current_user)):
    _assert_access(project_id, user)
    row = fetch_one("SELECT * FROM rsu_specific_activity_memorandum WHERE proyecto_id=%s AND tipo=%s", (project_id, kind))
    if not row:
        raise HTTPException(status_code=404, detail="El memorándum aún no ha sido emitido.")
    path = Path(str(row.get("archivo_pdf_url") or row.get("archivo_docx_url") or ""))
    if not path.exists():
        raise HTTPException(status_code=404, detail="No se encontró el archivo del memorándum.")
    title = _slug(_activity_payload(project_id)["titulo"])
    suffix = "memorandum_aprobacion" if kind == "aprobacion" else "memorandum_final"
    return FileResponse(str(path), filename=f"{title}-{suffix}{path.suffix}")


@router.get("/{project_id}/documentacion")
def complete_documentation(project_id: str, user: AuthUser = Depends(get_current_user)):
    activity = _assert_access(project_id, user)
    folder = _settings_root() / project_id / "expediente"
    folder.mkdir(parents=True, exist_ok=True)
    title = _slug(activity["titulo"])
    archive = folder / f"{title}-documentacion.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for annex in (1, 8, 9, 10):
            try:
                if annex in {1, 8, 10}:
                    submission = _current_submission(project_id, annex)
                    if not submission:
                        continue
                    revision = int(submission.get("revision") or 1)
                else:
                    row = fetch_one("SELECT COALESCE(MAX(revision),0) AS revision FROM rsu_annex_evaluation WHERE proyecto_id=%s AND anexo_numero=9", (project_id,))
                    revision = int((row or {}).get("revision") or 0)
                    if revision <= 0:
                        continue
                path = _pdf_path(project_id, annex, revision)
                if not path.exists():
                    generate_activity_pdf(annex, path, _annex_pdf_data(project_id, annex))
                zf.write(path, f"{title}-anexo-{annex}.pdf")
            except Exception:
                continue
        for memo in fetch_all("SELECT * FROM rsu_specific_activity_memorandum WHERE proyecto_id=%s", (project_id,)):
            path = Path(str(memo.get("archivo_pdf_url") or ""))
            if path.exists():
                zf.write(path, f"{title}-memorandum_{memo['tipo']}.pdf")
        for cert in fetch_all("SELECT c.archivo_url,CONCAT_WS(' ',p.nombres,p.apellidos) AS participante FROM certificado c INNER JOIN persona p ON p.persona_id=c.persona_id WHERE c.proyecto_id=%s", (project_id,)):
            path = Path(str(cert.get("archivo_url") or ""))
            if path.exists():
                zf.write(
                    path,
                    f"certificados/{certificate_download_filename(cert.get('participante'), activity.get('titulo'))}",
                )
        zf.writestr("LEEME.txt", f"Expediente de la actividad específica: {activity['titulo']}\nEstado: {activity['estado_flujo']}\n")
    return FileResponse(str(archive), media_type="application/zip", filename=archive.name)


@router.get("/certificados/{certificate_id}/descargar")
def download_certificate(certificate_id: str, user: AuthUser = Depends(get_current_user)):
    row = fetch_one(
        """SELECT c.*,pr.titulo,CONCAT_WS(' ',pe.nombres,pe.apellidos) AS nombre_completo
           FROM certificado c
           INNER JOIN proyecto pr ON pr.proyecto_id=c.proyecto_id
           INNER JOIN persona pe ON pe.persona_id=c.persona_id
           WHERE c.certificado_id=%s LIMIT 1""",
        (certificate_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Certificado no encontrado.")
    if not _can_access(str(row["proyecto_id"]), user):
        raise HTTPException(status_code=403, detail="No tienes acceso a este certificado.")
    path = Path(str(row.get("archivo_url") or ""))
    if not path.exists():
        raise HTTPException(status_code=404, detail="No se encontró el archivo del certificado.")
    return FileResponse(
        str(path),
        filename=certificate_download_filename(row.get("nombre_completo"), row.get("titulo")),
        media_type="application/pdf",
    )
