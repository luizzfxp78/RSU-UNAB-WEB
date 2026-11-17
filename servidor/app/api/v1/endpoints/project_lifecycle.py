from __future__ import annotations

import base64
import binascii
import json
import zipfile
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.api.deps import get_current_user, require_admin, require_project_reviewer, deny_certificate_for_simulation
from app.core.config import get_settings
from app.db.mysql import fetch_all, fetch_one, get_connection
from app.schemas.auth import AuthUser
from app.schemas.project_lifecycle import (
    Annex12In,
    Annex3In,
    Annex5In,
    Annex6In,
    AnnexEvaluationIn,
    CertificateIssueIn,
    EncodedFileIn,
    IncidentIn,
    IncidentReviewIn,
    ResolutionActIn,
    StartExecutionIn,
    WorkflowNoteIn,
)
from app.services.annex_pdf import annex_pdf_fingerprint, ensure_annex_pdf_cached
from app.services.memorandum_service import generate_certificate_memorandum, generate_memorandum
from app.services.certificate_pdf import generate_certificate_pdfs_batch
from app.services.download_filenames import certificate_download_filename
from app.services.websocket_manager import ws_manager
from app.services.notification_service import (
    notify_admins,
    notify_project_responsibles,
    notify_users,
    project_user_ids,
)

router = APIRouter(prefix="/ciclo-rsu", tags=["Ciclo completo de proyectos RSU"])

ANNEX4_CRITERIA = {
    "linea_accion": "Señala la línea de acción de RSU",
    "resumen": "Resumen completo con objetivo, metodología, población y resultados esperados",
    "problematica": "Problemática relacionada con la línea de acción",
    "objetivo": "Objetivo fundamental del proyecto",
    "metodologia": "Estrategias o metodología de aplicación",
    "impacto_social": "Impacto y beneficio para la comunidad",
    "impacto_estudiantes": "Contribución formativa en estudiantes colaboradores",
    "poblacion": "Muestra, población y entorno objetivo",
    "cronograma": "Cronograma aplicable durante el ciclo académico",
    "presupuesto": "Presupuesto coherente con la convocatoria",
}
ANNEX7_CRITERIA = {
    "resumen_evidencias": "Resumen y evidencia de labores realizadas",
    "dificultades": "Dificultades encontradas",
    "logros": "Oportunidades y/o logros alcanzados",
    "recomendaciones": "Recomendaciones y/o sugerencias",
    "indicadores": "Indicadores de logro de resultados",
}


def _safe_filename_component(value: Any, fallback: str = "proyecto") -> str:
    normalized = unicodedata.normalize("NFKD", str(value or fallback))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_text).strip("_-")
    return (safe or fallback)[:100]


def _is_admin(user: AuthUser) -> bool:
    role = str(user.rol_sistema or "").strip().lower()
    if role:
        return role in {"administrador_rsu", "director_rsu"}
    return user.tipo_usuario.strip().lower() in {"administrativo", "administrador", "admin"}


def _is_administrador_rsu(user: AuthUser) -> bool:
    role = str(user.rol_sistema or "").strip().lower()
    if role:
        return role == "administrador_rsu"
    # Compatibilidad exclusiva con cuentas antiguas sin rol funcional.
    return user.tipo_usuario.strip().lower() in {"administrador", "admin"}


def _state_id(entity: str, name: str) -> str:
    row = fetch_one(
        "SELECT catalogo_estado_id FROM catalogo_estado WHERE entidad=%s AND LOWER(nombre)=LOWER(%s) LIMIT 1",
        (entity, name),
    )
    if row is None:
        raise HTTPException(status_code=400, detail=f"No existe el estado {entity}:{name}.")
    return str(row["catalogo_estado_id"])


def _role_id(name: str) -> str:
    row = fetch_one("SELECT rol_id FROM catalogo_rol WHERE LOWER(nombre)=LOWER(%s) AND activo=1 LIMIT 1", (name,))
    if row is None:
        raise HTTPException(status_code=400, detail=f"No existe el rol de proyecto '{name}'.")
    return str(row["rol_id"])


def _json_load(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _weekly_activity_dict(value: Any) -> dict[str, str]:
    """Normaliza actividades semanales almacenadas como lista o diccionario."""
    result: dict[str, str] = {}
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                week = int(item.get("semana") or 0)
            except (TypeError, ValueError):
                continue
            if week:
                result[str(week)] = str(item.get("actividad") or item.get("detalle") or "")
    elif isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key).lower().replace("semana_", "").replace("semana ", "")
            try:
                week = int(key)
            except (TypeError, ValueError):
                continue
            result[str(week)] = str(
                raw_value.get("actividad") if isinstance(raw_value, dict) else raw_value or ""
            )
    return result


def _is_business_day(day: date) -> bool:
    row = fetch_one("SELECT es_laborable FROM calendario_laboral WHERE fecha=%s", (day,))
    if row is not None:
        return bool(row["es_laborable"])
    return day.weekday() < 5


def _add_business_days(start: date, amount: int) -> date:
    current = start
    remaining = amount
    while remaining > 0:
        current += timedelta(days=1)
        if _is_business_day(current):
            remaining -= 1
    return current


def _person_kind(person_id: str) -> str:
    row = fetch_one(
        """
        SELECT CASE
          WHEN d.persona_id IS NOT NULL THEN 'docente'
          WHEN e.persona_id IS NOT NULL THEN 'estudiante'
          WHEN af.tipo_vinculo IS NOT NULL THEN af.tipo_vinculo
          WHEN a.persona_id IS NOT NULL THEN 'no_docente'
          ELSE 'persona' END AS tipo
        FROM persona p
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        WHERE p.persona_id=%s AND p.activo=1
        """,
        (person_id,),
    )
    if row is None:
        raise HTTPException(status_code=400, detail="La persona seleccionada no existe o está inactiva.")
    return str(row["tipo"])


def _responsible_id(project_id: str) -> str | None:
    row = fetch_one(
        """
        SELECT rp.persona_id
        FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        WHERE rp.proyecto_id=%s AND rp.activo=1 AND LOWER(cr.nombre)='responsable'
        LIMIT 1
        """,
        (project_id,),
    )
    return str(row["persona_id"]) if row else None


def _person_faculty(person_id: str) -> str:
    row = fetch_one(
        """
        SELECT COALESCE(da_d.facultad,da_e.facultad,da_a.facultad,ap.facultad,a.area,'') AS facultad
        FROM persona p
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_d ON da_d.departamento_academico_id=d.departamento_academico_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_e ON da_e.departamento_academico_id=e.departamento_academico_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_a ON da_a.departamento_academico_id=af.departamento_academico_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
        WHERE p.persona_id=%s AND p.activo=1 LIMIT 1
        """,
        (person_id,),
    )
    return str((row or {}).get("facultad") or "").strip()


def _automatic_annex3_content(
    *,
    title: str,
    line_action_id: str,
    responsible_person_id: str,
    members: Iterable[Any],
) -> dict[str, Any]:
    team = [{"persona_id": responsible_person_id, "role": "responsable"}]
    for member in members:
        role = str(getattr(member, "role", None) or (member.get("role") if isinstance(member, dict) else "colaborador"))
        person_id = str(getattr(member, "persona_id", None) or (member.get("persona_id") if isinstance(member, dict) else ""))
        if person_id:
            team.append({"persona_id": person_id, "role": role})
    return {
        "titulo": title,
        "linea_accion_id": line_action_id,
        "miembros": team,
        "observaciones_registro": "Generado automáticamente con los datos de los Anexos 1 y 2.",
        "generado_automaticamente": True,
    }


def _annex4_authorities(project_id: str) -> dict[str, dict[str, Any]]:
    faculty = _project_faculty(project_id)
    return {
        "directora_rsu": _authority_by_role("director_rsu") or {},
        "director_facultad": _authority_by_role("director_facultad", faculty) or {},
    }


def _validate_annex4_authorities(project_id: str) -> dict[str, dict[str, Any]]:
    authorities = _annex4_authorities(project_id)
    director_rsu = authorities["directora_rsu"]
    faculty_director = authorities["director_facultad"]
    faculty = _project_faculty(project_id)
    if not director_rsu:
        raise HTTPException(status_code=400, detail="Debe existir una Directora o Director de RSU activo para firmar el Anexo 4.")
    if not str(director_rsu.get("firma_url") or "").strip():
        raise HTTPException(status_code=400, detail="La Directora o Director de RSU debe tener una firma institucional registrada.")
    if not faculty_director:
        raise HTTPException(status_code=400, detail=f"Debe existir un Director de Facultad activo para {faculty or 'la facultad del proyecto'}.")
    if not str(faculty_director.get("firma_url") or "").strip():
        raise HTTPException(status_code=400, detail=f"El Director de Facultad de {faculty or 'la facultad del proyecto'} debe tener una firma institucional registrada.")
    return authorities


def _validate_memorandum_prerequisites(project_id: str) -> dict[str, Any]:
    """Comprueba las autoridades necesarias antes de aceptar una aprobación final."""
    _validate_annex4_authorities(project_id)
    vice = _authority_by_role("vicepresidente_academico")
    if not vice:
        raise HTTPException(
            status_code=400,
            detail="Registra un Vicepresidente académico activo antes de aprobar el proyecto y emitir el memorándum.",
        )
    template = Path(__file__).resolve().parents[4] / "official_annex_templates" / "Memorandum_087-2026_UNAB.docx"
    if not template.exists():
        raise HTTPException(status_code=500, detail="No se encontró la plantilla oficial del memorándum de aprobación.")
    return vice


def _project_faculty(project_id: str) -> str:
    row = fetch_one(
        """
        SELECT COALESCE(da_d.facultad,da_e.facultad,da_a.facultad,ap.facultad,a.area,'') AS facultad
        FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id AND LOWER(cr.nombre)='responsable'
        INNER JOIN persona p ON p.persona_id=rp.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_d ON da_d.departamento_academico_id=d.departamento_academico_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_e ON da_e.departamento_academico_id=e.departamento_academico_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_a ON da_a.departamento_academico_id=af.departamento_academico_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
        WHERE rp.proyecto_id=%s AND rp.activo=1 LIMIT 1
        """,
        (project_id,),
    )
    return str((row or {}).get("facultad") or "").strip()


def _active_review_assignment(
    project_id: str,
    person_id: str,
    annex_number: int | None = None,
) -> dict[str, Any] | None:
    annex_filter = " AND anexo_numero=%s" if annex_number is not None else ""
    params: tuple[Any, ...] = (
        (project_id, person_id, annex_number)
        if annex_number is not None
        else (project_id, person_id)
    )
    return fetch_one(
        f"""SELECT * FROM rsu_project_review_assignment
        WHERE proyecto_id=%s AND revisor_persona_id=%s AND activo=1{annex_filter}
        ORDER BY revision DESC LIMIT 1""",
        params,
    )


def _review_assignment_for_user(
    project_id: str,
    user: AuthUser,
    annex_number: int,
) -> dict[str, Any] | None:
    """Obtiene la revisión del usuario.

    El Director de Facultad conserva una asignación nominativa. El cupo de
    Administración RSU es compartido: cualquier administrador o director RSU
    puede tomar la revisión pendiente y continuar el seguimiento, incluso tras
    una subsanación realizada por otro administrador.
    """
    exact = _active_review_assignment(project_id, user.persona_id, annex_number)
    if exact:
        return exact
    if not _is_admin(user):
        return None
    return fetch_one(
        """SELECT * FROM rsu_project_review_assignment
        WHERE proyecto_id=%s AND anexo_numero=%s AND rol_revisor='administrador_rsu'
          AND activo=1 AND estado='pendiente'
        ORDER BY revision DESC LIMIT 1""",
        (project_id, annex_number),
    )


def _review_revision(project_id: str, annex_number: int = 4) -> int:
    row = fetch_one(
        """SELECT COALESCE(MAX(revision),0) AS revision
        FROM rsu_project_review_assignment
        WHERE proyecto_id=%s AND anexo_numero=%s""",
        (project_id, annex_number),
    )
    return int((row or {}).get("revision") or 0)


def _authority_by_role(role_code: str, faculty: str | None = None) -> dict[str, Any] | None:
    params: list[Any] = [role_code]
    faculty_filter = ""
    if faculty:
        faculty_filter = " AND LOWER(TRIM(COALESCE(usr.facultad,ap.facultad,'')))=LOWER(TRIM(%s))"
        params.append(faculty)
    return fetch_one(
        f"""
        SELECT u.usuario_id,p.persona_id,CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo,p.dni,
               COALESCE(c.email_institucional,c.email_personal,'') AS correo,c.telefono,c.direccion,
               sr.codigo AS rol_sistema,COALESCE(usr.facultad,ap.facultad,'') AS facultad,
               COALESCE(ap.cargo_oficial,a.cargo,sr.nombre) AS cargo_oficial,
               COALESCE(ap.oficina,a.area,'') AS oficina,ap.firma_url
        FROM rsu_user_system_role usr
        INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id
        INNER JOIN usuario u ON u.usuario_id=usr.usuario_id AND u.activo=1
        INNER JOIN persona p ON p.persona_id=u.persona_id AND p.activo=1
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
        WHERE sr.codigo=%s AND usr.activo=1 AND sr.activo=1 {faculty_filter}
        ORDER BY usr.fecha_asignacion DESC LIMIT 1
        """,
        tuple(params),
    )


def _assign_reviewers(
    project_id: str,
    revision: int,
    annex_number: int = 4,
) -> list[dict[str, Any]]:
    faculty = _project_faculty(project_id)
    faculty_director = _authority_by_role("director_facultad", faculty) if faculty else None
    if not faculty_director:
        raise HTTPException(
            status_code=400,
            detail=f"No existe un Director de Facultad activo para {faculty or 'la facultad del proyecto'}.",
        )
    assignments = [
        # El cupo administrativo queda abierto para que cualquier administrador
        # RSU pueda asumirlo y continuar el seguimiento.
        ("administrador_rsu", None, None),
        ("director_facultad", faculty_director, faculty or None),
    ]
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE rsu_project_review_assignment SET activo=0 "
                "WHERE proyecto_id=%s AND anexo_numero=%s",
                (project_id, annex_number),
            )
            for role, reviewer, reviewer_faculty in assignments:
                cur.execute(
                    """INSERT INTO rsu_project_review_assignment
                    (assignment_id,proyecto_id,anexo_numero,revision,rol_revisor,revisor_persona_id,facultad,estado,activo)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'pendiente',1)""",
                    (
                        str(uuid4()), project_id, annex_number, revision, role,
                        (reviewer or {}).get("persona_id"), reviewer_faculty,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    return fetch_all(
        """SELECT a.*,CONCAT(p.nombres,' ',p.apellidos) AS revisor_nombre
        FROM rsu_project_review_assignment a
        LEFT JOIN persona p ON p.persona_id=a.revisor_persona_id
        WHERE a.proyecto_id=%s AND a.anexo_numero=%s AND a.revision=%s
        ORDER BY a.rol_revisor""",
        (project_id, annex_number, revision),
    )


def _next_memorandum_number() -> str:
    year = date.today().year
    row = fetch_one(
        "SELECT COUNT(*)+1 AS siguiente FROM rsu_project_memorandum WHERE YEAR(fecha_emision)=%s",
        (year,),
    )
    sequence = int((row or {}).get("siguiente") or 1)
    return f"MEMORÁNDUM {sequence:03d}-{year}-UNAB-VP. ACAD/DRS"


def _next_certificate_memorandum_number() -> str:
    year = date.today().year
    row = fetch_one(
        "SELECT COUNT(*)+1 AS siguiente FROM rsu_certificate_memorandum WHERE YEAR(fecha_emision)=%s",
        (year,),
    )
    sequence = int((row or {}).get("siguiente") or 1)
    return f"MEMORÁNDUM CERT-{sequence:03d}-{year}-UNAB-VP. ACAD/DRS"


def _director_signature(project_id: str, director_person_id: str | None) -> str | None:
    params: list[Any] = [project_id]
    person_filter = ""
    if director_person_id:
        person_filter = " AND persona_firmante_id=%s"
        params.append(director_person_id)
    row = fetch_one(
        f"""SELECT archivo_url FROM rsu_workflow_file
        WHERE proyecto_id=%s AND anexo_numero=4 AND proposito='firma_evaluador' {person_filter}
        ORDER BY fecha_subida DESC LIMIT 1""",
        tuple(params),
    )
    return str((row or {}).get("archivo_url") or "") or None


async def _issue_memorandum(project_id: str, emitted_by: AuthUser) -> dict[str, Any]:
    existing = fetch_one("SELECT * FROM rsu_project_memorandum WHERE proyecto_id=%s", (project_id,))
    if existing:
        return existing
    project = _assert_access(project_id, emitted_by)
    responsible = fetch_one(
        """SELECT p.persona_id,CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo,p.dni,
                  COALESCE(da_d.facultad,da_e.facultad,da_a.facultad,ap.facultad,a.area,'') AS facultad,
                  COALESCE(da_d.carrera,da_e.carrera,da_a.carrera,a.area,'') AS escuela
        FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id AND LOWER(cr.nombre)='responsable'
        INNER JOIN persona p ON p.persona_id=rp.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_d ON da_d.departamento_academico_id=d.departamento_academico_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_e ON da_e.departamento_academico_id=e.departamento_academico_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_a ON da_a.departamento_academico_id=af.departamento_academico_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
        WHERE rp.proyecto_id=%s AND rp.activo=1 LIMIT 1""",
        (project_id,),
    )
    if not responsible:
        raise HTTPException(status_code=400, detail="No se encontró al responsable del proyecto.")
    vice = _authority_by_role("vicepresidente_academico")
    if not vice:
        raise HTTPException(status_code=400, detail="Registra un perfil activo con el rol Vicepresidente académico antes de emitir el memorándum.")
    director = _authority_by_role("director_rsu") or _authority_by_role("administrador_rsu")
    if not director:
        raise HTTPException(status_code=400, detail="Registra un perfil activo para la Dirección de RSU.")
    signature_value = str(director.get("firma_url") or "") or _director_signature(project_id, str(director.get("persona_id") or ""))
    signature_path = str(_resolve_database_path(signature_value)) if signature_value else ""
    if not signature_path or not Path(signature_path).exists():
        raise HTTPException(status_code=400, detail="La directora de RSU debe tener una firma en formato de imagen para emitir el memorándum.")
    number = _next_memorandum_number()
    settings = get_settings()
    template = Path(__file__).resolve().parents[4] / "official_annex_templates" / "Memorandum_087-2026_UNAB.docx"
    output_dir = Path(settings.upload_dir) / "memorandums" / project_id
    docx_path, pdf_path = generate_memorandum(
        template_path=template,
        output_dir=output_dir,
        number=number,
        issue_date=date.today(),
        project=project,
        responsible=responsible,
        vice_president=vice,
        director=director,
        director_signature_path=signature_path,
    )
    if pdf_path is None or not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise HTTPException(
            status_code=500,
            detail=(
                "No se pudo generar el memorándum en PDF. La aprobación no se cerró; "
                "verifica LibreOffice o Microsoft Word y vuelve a aprobar el proyecto."
            ),
        )
    memorandum_id = str(uuid4())
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO rsu_project_memorandum
                (memorandum_id,proyecto_id,numero_memorandum,fecha_emision,tipo_proyecto,titulo_proyecto,
                 responsable_persona_id,vicepresidente_persona_id,directora_persona_id,archivo_docx_url,archivo_pdf_url,
                 emitido_por,fecha_entrega_responsable,fecha_entrega_vicepresidencia)
                VALUES (%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())""",
                (memorandum_id,project_id,number,project.get("tipo_proyecto") or "Proyecto RSU",project.get("titulo"),
                 responsible["persona_id"],vice.get("persona_id"),director.get("persona_id"),str(docx_path),
                 str(pdf_path) if pdf_path else None,emitted_by.persona_id),
            )
            cur.execute("UPDATE rsu_project_workflow SET fecha_acta_resolutiva=NOW() WHERE proyecto_id=%s", (project_id,))
            _set_workflow(cur, project_id, "inicio_ejecucion", "memorandum_emitido", emitted_by.persona_id, None, "Memorándum de aprobación emitido")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    recipients = project_user_ids(project_id, roles=["responsable"])
    if vice.get("usuario_id"):
        recipients.append(str(vice["usuario_id"]))
    await notify_users(
        recipients,
        notification_type="memorandum_emitido",
        title="Memorándum de aprobación emitido",
        message=f"Se emitió el {number} para el {str(project.get('tipo_proyecto') or 'proyecto').lower()} «{project.get('titulo')}». El responsable ya puede iniciar la ejecución.",
        module="Módulo de Proyectos",
        priority="alta",
        entity_type="memorandum",
        entity_id=memorandum_id,
        project_id=project_id,
        data={"proyecto_id": project_id, "memorandum_id": memorandum_id, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:memorandum:{number}",
        generated_by=emitted_by.nombre_completo,
        important=True,
    )
    return fetch_one("SELECT * FROM rsu_project_memorandum WHERE memorandum_id=%s", (memorandum_id,)) or {}


def _final_memorandum_context(project_id: str, user: AuthUser) -> dict[str, Any]:
    project = _assert_access(project_id, user)
    responsible = fetch_one(
        """SELECT p.persona_id,CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo,p.dni,
                  COALESCE(da_d.facultad,da_e.facultad,da_a.facultad,ap.facultad,a.area,'') AS facultad,
                  COALESCE(da_d.carrera,da_e.carrera,da_a.carrera,a.area,'') AS escuela
           FROM rol_proyecto rp
           INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id AND LOWER(cr.nombre)='responsable'
           INNER JOIN persona p ON p.persona_id=rp.persona_id
           LEFT JOIN docente d ON d.persona_id=p.persona_id
           LEFT JOIN departamento_academico da_d ON da_d.departamento_academico_id=d.departamento_academico_id
           LEFT JOIN estudiante e ON e.persona_id=p.persona_id
           LEFT JOIN departamento_academico da_e ON da_e.departamento_academico_id=e.departamento_academico_id
           LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
           LEFT JOIN departamento_academico da_a ON da_a.departamento_academico_id=af.departamento_academico_id
           LEFT JOIN administrativo a ON a.persona_id=p.persona_id
           LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
           WHERE rp.proyecto_id=%s AND rp.activo=1 LIMIT 1""",
        (project_id,),
    )
    if not responsible:
        raise HTTPException(status_code=400, detail="No se encontró al responsable del proyecto.")
    vice = _authority_by_role("vicepresidente_academico")
    director = _authority_by_role("director_rsu") or _authority_by_role("administrador_rsu")
    if not vice:
        raise HTTPException(status_code=400, detail="Registra un Vicepresidente académico activo antes de aprobar el Anexo 7.")
    if not director:
        raise HTTPException(status_code=400, detail="Registra un perfil activo para la Dirección de RSU.")
    signature_value = str(director.get("firma_url") or "") or _director_signature(project_id, str(director.get("persona_id") or ""))
    signature_path = _resolve_database_path(signature_value) if signature_value else Path()
    if not signature_value or not signature_path.exists():
        raise HTTPException(status_code=400, detail="La Dirección de RSU debe tener una firma institucional en imagen para emitir el memorándum final.")
    template = Path(__file__).resolve().parents[4] / "official_annex_templates" / "Memorandum_087-2026_UNAB.docx"
    if not template.exists():
        raise HTTPException(status_code=500, detail="No se encontró la plantilla oficial del memorándum.")
    return {
        "project": project, "responsible": responsible, "vice": vice, "director": director,
        "signature_path": signature_path, "template": template,
    }


async def _issue_final_approval_memorandum(project_id: str, emitted_by: AuthUser) -> dict[str, Any]:
    existing = fetch_one("SELECT * FROM rsu_certificate_memorandum WHERE proyecto_id=%s", (project_id,))
    if existing:
        return existing
    context = _final_memorandum_context(project_id, emitted_by)
    project = context["project"]
    responsible = context["responsible"]
    vice = context["vice"]
    director = context["director"]
    number = _next_certificate_memorandum_number()
    output_dir = _workflow_storage_root() / "private_rsu_workflow" / project_id / "memorandums_finales"
    docx_path, pdf_path = generate_certificate_memorandum(
        template_path=context["template"],
        output_dir=output_dir,
        number=number,
        issue_date=date.today(),
        project=project,
        responsible=responsible,
        vice_president=vice,
        director=director,
        director_signature_path=str(context["signature_path"]),
        certificate_count=0,
        purpose="final_approval",
    )
    if pdf_path is None or not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise HTTPException(status_code=500, detail="No se pudo generar el memorándum de aprobación final en PDF.")
    memo_id = str(uuid4())
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO rsu_certificate_memorandum
                (certificate_memorandum_id,proyecto_id,numero_memorandum,fecha_emision,titulo_proyecto,
                 cantidad_certificados,vicepresidente_persona_id,directora_persona_id,archivo_docx_url,
                 archivo_pdf_url,emitido_por,fecha_entrega_vicepresidencia)
                VALUES (%s,%s,%s,CURDATE(),%s,0,%s,%s,%s,%s,%s,NOW())""",
                (
                    memo_id, project_id, number, project.get("titulo"), vice.get("persona_id"),
                    director.get("persona_id"), _path_for_database(docx_path), _path_for_database(pdf_path),
                    emitted_by.persona_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    recipients = project_user_ids(project_id, roles=["responsable"])
    if vice.get("usuario_id"):
        recipients.append(str(vice["usuario_id"]))
    await notify_users(
        recipients,
        notification_type="memorandum_aprobacion_final_emitido",
        title="Memorándum de aprobación final emitido",
        message=f"Se emitió el {number} tras la aprobación del Anexo 7 del proyecto «{project.get('titulo')}».",
        module="Módulo de Proyectos",
        priority="alta",
        entity_type="memorandum_final",
        entity_id=memo_id,
        project_id=project_id,
        data={"proyecto_id": project_id, "memorandum_certificacion_id": memo_id, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:memorandum-final:{number}",
        generated_by=emitted_by.nombre_completo,
        important=True,
    )
    return fetch_one("SELECT * FROM rsu_certificate_memorandum WHERE certificate_memorandum_id=%s", (memo_id,)) or {}


def _assert_access(project_id: str, user: AuthUser, *, responsible_only: bool = False, admin_only: bool = False) -> dict[str, Any]:
    project = fetch_one(
        """
        SELECT p.*, ce.nombre AS estado_proyecto, la.codigo AS linea_codigo, la.nombre AS linea_accion,
               pa.nombre AS periodo_academico, cv.fecha_inicio AS convocatoria_inicio,
               cv.fecha_limite_presentacion, tp.nombre AS tipo_proyecto,
               wf.etapa_actual, wf.estado_flujo, wf.fecha_limite_actual,
               wf.fecha_inicio_ejecucion, wf.fecha_semana_9, wf.fecha_semana_16,
               wf.anexo5_habilitado_manual, wf.anexo6_habilitado_manual,
               wf.fecha_habilitacion_anexo5, wf.fecha_habilitacion_anexo6,
               wf.eliminado_logico, wf.motivo_cierre
        FROM proyecto p
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=p.periodo_academico_id
        INNER JOIN convocatoria cv ON cv.convocatoria_id=p.convocatoria_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        LEFT JOIN rsu_project_workflow wf ON wf.proyecto_id=p.proyecto_id
        WHERE p.proyecto_id=%s
        """,
        (project_id,),
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    admin = _is_admin(user)
    responsible = _responsible_id(project_id) == user.persona_id
    participant = fetch_one(
        "SELECT 1 FROM rol_proyecto WHERE proyecto_id=%s AND persona_id=%s AND activo=1 LIMIT 1",
        (project_id, user.persona_id),
    ) is not None
    reviewer = _active_review_assignment(project_id, user.persona_id) is not None
    historical_reviewer = fetch_one(
        "SELECT 1 FROM rsu_project_review_assignment "
        "WHERE proyecto_id=%s AND revisor_persona_id=%s LIMIT 1",
        (project_id, user.persona_id),
    ) is not None
    # El Director de Facultad conserva acceso de consulta a los expedientes de su
    # propia facultad incluso después de finalizar su revisión. Esto permite
    # mostrar el expediente actualizado tras aprobar u observar, sin depender de
    # que la asignación de revisión continúe activa.
    project_faculty = _project_faculty(project_id)
    assigned_faculty = str(user.facultad_asignada or "").strip().casefold()
    faculty_director = (
        str(user.rol_sistema or "").strip().lower() == "director_facultad"
        and bool(assigned_faculty)
        and assigned_faculty == str(project_faculty or "").strip().casefold()
    )
    vice = False
    if str(user.rol_sistema or "").lower() == "vicepresidente_academico":
        vice = fetch_one(
            """SELECT 1 AS ok FROM rsu_project_memorandum
            WHERE proyecto_id=%s AND vicepresidente_persona_id=%s
            UNION ALL
            SELECT 1 AS ok FROM rsu_certificate_memorandum
            WHERE proyecto_id=%s AND vicepresidente_persona_id=%s
            LIMIT 1""",
            (project_id, user.persona_id, project_id, user.persona_id),
        ) is not None
    if admin_only and not admin:
        raise HTTPException(status_code=403, detail="Esta operación corresponde a la Dirección de RSU.")
    if responsible_only and not responsible:
        raise HTTPException(status_code=403, detail="Solo el representante del proyecto puede realizar esta operación.")
    if not admin and not participant and not reviewer and not historical_reviewer and not faculty_director and not vice:
        raise HTTPException(status_code=403, detail="No tienes acceso a este expediente.")
    _expire_if_needed(project)
    refreshed = fetch_one(
        """SELECT etapa_actual, estado_flujo, fecha_limite_actual,
                  anexo5_habilitado_manual, anexo6_habilitado_manual,
                  fecha_habilitacion_anexo5, fecha_habilitacion_anexo6,
                  eliminado_logico, motivo_cierre
           FROM rsu_project_workflow WHERE proyecto_id=%s""",
        (project_id,),
    ) or {}
    project.update(refreshed)
    return project


def _history(cur: Any, project_id: str, old_stage: str | None, new_stage: str, old_state: str | None, new_state: str, person_id: str | None, comment: str | None = None) -> None:
    cur.execute(
        """
        INSERT INTO rsu_workflow_history
        (history_id, proyecto_id, etapa_anterior, etapa_nueva, estado_anterior, estado_nuevo, cambiado_por, comentario)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (str(uuid4()), project_id, old_stage, new_stage, old_state, new_state, person_id, comment),
    )


def _set_workflow(cur: Any, project_id: str, new_stage: str, new_state: str, person_id: str | None, deadline: date | None = None, comment: str | None = None) -> None:
    current = fetch_one("SELECT etapa_actual, estado_flujo FROM rsu_project_workflow WHERE proyecto_id=%s", (project_id,)) or {}
    cur.execute(
        """
        UPDATE rsu_project_workflow
        SET etapa_actual=%s, estado_flujo=%s, fecha_limite_actual=%s
        WHERE proyecto_id=%s
        """,
        (new_stage, new_state, deadline, project_id),
    )
    cur.execute("UPDATE proyecto SET estado_id=%s WHERE proyecto_id=%s", (_state_id("proyecto", new_state), project_id))
    _history(cur, project_id, current.get("etapa_actual"), new_stage, current.get("estado_flujo"), new_state, person_id, comment)


def _expire_if_needed(project: dict[str, Any]) -> None:
    deadline = project.get("fecha_limite_actual")
    state = str(project.get("estado_flujo") or "")
    if not deadline or state not in {"con_observaciones", "observacion_final"}:
        return
    deadline_date = deadline if isinstance(deadline, date) else date.fromisoformat(str(deadline))
    if date.today() <= deadline_date:
        return
    project_id = str(project["proyecto_id"])
    new_state = "no_presentado" if state == "con_observaciones" else "desaprobado"
    new_stage = "archivado" if state == "con_observaciones" else "cierre_desaprobado"
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            _set_workflow(cur, project_id, new_stage, new_state, None, None, "Plazo de subsanación vencido")
            cur.execute(
                "UPDATE rsu_project_workflow SET eliminado_logico=%s, motivo_cierre=%s WHERE proyecto_id=%s",
                (1 if new_state == "no_presentado" else 0, "Plazo de subsanación vencido", project_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def _validate_encoded_file(item: EncodedFileIn) -> tuple[bytes, str]:
    if not item.data_base64:
        raise HTTPException(status_code=400, detail=f"El archivo {item.label} no contiene datos para cargar.")
    try:
        content = base64.b64decode(item.data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"El archivo {item.label} no tiene un formato válido.") from exc
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"El archivo {item.label} supera 10 MB.")
    extension = Path(item.filename).suffix.lower()
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
    if extension not in allowed:
        raise HTTPException(status_code=400, detail="Solo se admiten imágenes PNG/JPG/WEBP o PDF.")
    return content, extension


def _workflow_storage_root() -> Path:
    return get_settings().upload_dir.resolve().parent


def _path_for_database(path: Path) -> str:
    """Guarda una ruta corta y portable para evitar errores VARCHAR(255)."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(_workflow_storage_root()).as_posix()
    except ValueError:
        return str(resolved)


def _resolve_database_path(value: Any) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else (_workflow_storage_root() / path)


def _store_file(project_id: str, annex_number: int | None, purpose: str, item: EncodedFileIn, uploader: str) -> str:
    if item.workflow_file_id:
        existing = fetch_one(
            "SELECT workflow_file_id, proyecto_id FROM rsu_workflow_file WHERE workflow_file_id=%s",
            (item.workflow_file_id,),
        )
        if existing is None or str(existing.get("proyecto_id") or "") != project_id:
            raise HTTPException(status_code=400, detail="La firma guardada no pertenece a este proyecto.")
        return str(existing["workflow_file_id"])
    content, extension = _validate_encoded_file(item)
    file_id = str(uuid4())
    base = get_settings().upload_dir.resolve().parent / "private_rsu_workflow" / project_id / (f"anexo_{annex_number}" if annex_number else purpose)
    base.mkdir(parents=True, exist_ok=True)
    output = base / f"{file_id}{extension}"
    output.write_bytes(content)
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO rsu_workflow_file
                (workflow_file_id, proyecto_id, anexo_numero, proposito, etiqueta, persona_firmante_id,
                 archivo_url, nombre_archivo, mime_type, subido_por)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    file_id,
                    project_id,
                    annex_number,
                    purpose,
                    item.label,
                    item.person_id or (uploader if "firma" in purpose.lower() else None),
                    str(output),
                    item.filename,
                    item.mime_type,
                    uploader,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            output.unlink(missing_ok=True)
            raise
        finally:
            cur.close()
    return file_id


def _store_files(project_id: str, annex_number: int | None, purpose: str, files: Iterable[EncodedFileIn], uploader: str) -> list[str]:
    return [_store_file(project_id, annex_number, purpose, item, uploader) for item in files]


def _next_revision(project_id: str, annex_number: int) -> int:
    row = fetch_one(
        "SELECT COALESCE(MAX(revision),0)+1 AS next_revision FROM rsu_annex_submission WHERE proyecto_id=%s AND anexo_numero=%s",
        (project_id, annex_number),
    )
    return int(row["next_revision"] if row else 1)


def _save_annex(cur: Any, project_id: str, annex_number: int, person_id: str, content: dict[str, Any], status: str = "presentado") -> int:
    # La revisión se calcula dentro de la misma transacción. Así, si el
    # administrador y el Director de Facultad responden casi al mismo tiempo,
    # no intentan insertar la misma revisión del Anexo 4.
    cur.execute("SELECT proyecto_id FROM proyecto WHERE proyecto_id=%s FOR UPDATE", (project_id,))
    if cur.fetchone() is None:
        raise ValueError("No se encontró el proyecto para registrar el anexo.")
    cur.execute(
        "SELECT COALESCE(MAX(revision),0)+1 AS next_revision "
        "FROM rsu_annex_submission WHERE proyecto_id=%s AND anexo_numero=%s",
        (project_id, annex_number),
    )
    row = cur.fetchone()
    # Los cursores transaccionales de mysql.connector devuelven tuplas por
    # defecto. La versión anterior trataba el resultado como diccionario y
    # provocaba: AttributeError: 'tuple' object has no attribute 'get'.
    # Admitimos ambos formatos para que el registro y la subsanación funcionen
    # con cursores normales o dictionary=True.
    if isinstance(row, dict):
        next_revision = row.get("next_revision")
    elif isinstance(row, (tuple, list)):
        next_revision = row[0] if row else 1
    else:
        next_revision = 1
    revision = int(next_revision or 1)
    cur.execute(
        "UPDATE rsu_annex_submission SET es_actual=0 WHERE proyecto_id=%s AND anexo_numero=%s",
        (project_id, annex_number),
    )
    cur.execute(
        """
        INSERT INTO rsu_annex_submission
        (submission_id, proyecto_id, anexo_numero, revision, presentado_por, contenido_json, estado, es_actual)
        VALUES (%s,%s,%s,%s,%s,%s,%s,1)
        """,
        (str(uuid4()), project_id, annex_number, revision, person_id, json.dumps(content, ensure_ascii=False, default=str), status),
    )
    return revision


def _team_details(project_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT rp.persona_id, CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo, p.dni,
               cr.nombre AS rol, rp.horas_semanales, rp.horas_acumuladas,
               COALESCE(c.email_institucional,c.email_personal,'') AS correo,
               COALESCE(c.telefono,'') AS telefono,
               COALESCE(da_d.carrera, da_e.carrera, da_a.carrera, ap.facultad, a.area, '') AS escuela,
               COALESCE(da_d.facultad, da_e.facultad, da_a.facultad, ap.facultad, a.area, '') AS facultad,
               CASE WHEN d.persona_id IS NOT NULL THEN 'Docente'
                    WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                    WHEN af.tipo_vinculo='egresado' THEN 'Egresado'
                    WHEN af.tipo_vinculo='graduado' THEN 'Graduado'
                    WHEN af.tipo_vinculo='no_docente' OR a.persona_id IS NOT NULL THEN 'No docente'
                    ELSE 'Persona' END AS tipo_persona,
               COALESCE(e.codigo_estudiante,d.codigo_docente,af.codigo_institucional,p.dni) AS codigo
        FROM rol_proyecto rp
        INNER JOIN persona p ON p.persona_id=rp.persona_id
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_d ON da_d.departamento_academico_id=d.departamento_academico_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_e ON da_e.departamento_academico_id=e.departamento_academico_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        LEFT JOIN departamento_academico da_a ON da_a.departamento_academico_id=af.departamento_academico_id
        LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
        WHERE rp.proyecto_id=%s AND rp.activo=1
        ORDER BY CASE LOWER(cr.nombre) WHEN 'responsable' THEN 0 WHEN 'corresponsable' THEN 1 ELSE 2 END,
                 p.apellidos,p.nombres
        """,
        (project_id,),
    )


def _annexes(project_id: str) -> dict[str, Any]:
    rows = fetch_all(
        """
        SELECT anexo_numero, revision, contenido_json, estado, fecha_presentacion
        FROM rsu_annex_submission
        WHERE proyecto_id=%s AND es_actual=1
        ORDER BY anexo_numero
        """,
        (project_id,),
    )
    return {
        str(row["anexo_numero"]): {
            "revision": row["revision"],
            "estado": row["estado"],
            "fecha_presentacion": row["fecha_presentacion"],
            "contenido": _json_load(row["contenido_json"], {}),
        }
        for row in rows
    }


def _serialize(project_id: str, user: AuthUser) -> dict[str, Any]:
    project = _assert_access(project_id, user)
    project["equipo"] = _team_details(project_id)
    project["anexos"] = _annexes(project_id)
    project["evaluaciones"] = fetch_all(
        """
        SELECT ae.*, CONCAT(p.nombres,' ',p.apellidos) AS evaluador,
               ra.revision AS revision_ciclo,
               ra.assignment_id AS asignacion_revision_id
        FROM rsu_annex_evaluation ae
        INNER JOIN persona p ON p.persona_id=ae.evaluador_persona_id
        LEFT JOIN rsu_project_review_assignment ra ON ra.evaluation_id=ae.evaluation_id
        WHERE ae.proyecto_id=%s
        ORDER BY COALESCE(ra.revision,0) DESC, ae.fecha_evaluacion DESC, ae.evaluation_id DESC
        """,
        (project_id,),
    )
    for item in project["evaluaciones"]:
        raw_criteria = _json_load(item.pop("criterios_json", None), {})
        if isinstance(raw_criteria, dict):
            item["observaciones_por_criterio"] = dict(raw_criteria.pop("__observaciones_por_criterio", {}) or {})
            item["criterios"] = raw_criteria
        else:
            item["observaciones_por_criterio"] = {}
            item["criterios"] = {}
    observed_evaluations = [
        item for item in project["evaluaciones"]
        if int(item.get("anexo_numero") or 0) == 4
        and str(item.get("resultado") or "").lower() == "con_observaciones"
    ]
    project["observaciones_pendientes"] = (
        observed_evaluations[0]
        if observed_evaluations and str(project.get("estado_flujo") or "").lower() == "con_observaciones"
        else None
    )
    project["archivos"] = fetch_all(
        """
        SELECT workflow_file_id, anexo_numero, proposito, etiqueta, persona_firmante_id,
               nombre_archivo, mime_type, fecha_subida
        FROM rsu_workflow_file WHERE proyecto_id=%s ORDER BY fecha_subida DESC
        """,
        (project_id,),
    )
    project["acta_resolutiva"] = fetch_one("SELECT * FROM rsu_resolution_act WHERE proyecto_id=%s", (project_id,))
    project["revisores"] = fetch_all(
        """SELECT a.*,CONCAT(p.nombres,' ',p.apellidos) AS revisor_nombre
        FROM rsu_project_review_assignment a
        LEFT JOIN persona p ON p.persona_id=a.revisor_persona_id
        WHERE a.proyecto_id=%s AND a.activo=1 ORDER BY a.rol_revisor""",
        (project_id,),
    )
    project["memorandum"] = fetch_one(
        """SELECT m.*,CONCAT(v.nombres,' ',v.apellidos) AS vicepresidente_nombre,
                  CONCAT(d.nombres,' ',d.apellidos) AS directora_nombre
        FROM rsu_project_memorandum m
        LEFT JOIN persona v ON v.persona_id=m.vicepresidente_persona_id
        LEFT JOIN persona d ON d.persona_id=m.directora_persona_id
        WHERE m.proyecto_id=%s LIMIT 1""",
        (project_id,),
    )
    project["memorandum_certificacion"] = fetch_one(
        """SELECT m.*,CONCAT(v.nombres,' ',v.apellidos) AS vicepresidente_nombre,
                  CONCAT(d.nombres,' ',d.apellidos) AS directora_nombre
        FROM rsu_certificate_memorandum m
        LEFT JOIN persona v ON v.persona_id=m.vicepresidente_persona_id
        LEFT JOIN persona d ON d.persona_id=m.directora_persona_id
        WHERE m.proyecto_id=%s LIMIT 1""",
        (project_id,),
    )
    authority_signers = _annex4_authorities(project_id)
    project["autoridades_anexo4"] = {
        key: {
            "persona_id": value.get("persona_id"),
            "nombre_completo": value.get("nombre_completo"),
            "cargo_oficial": value.get("cargo_oficial"),
            "facultad": value.get("facultad"),
            "firma_registrada": bool(value.get("firma_url")),
        }
        for key, value in authority_signers.items()
    }
    project["incidencias"] = fetch_all(
        """
        SELECT i.*, CONCAT(p.nombres,' ',p.apellidos) AS reportado_por_nombre,
               CONCAT(m.nombres,' ',m.apellidos) AS miembro_afectado_nombre,
               CONCAT(r.nombres,' ',r.apellidos) AS reemplazo_nombre
        FROM rsu_project_incident i
        INNER JOIN persona p ON p.persona_id=i.reportado_por
        LEFT JOIN persona m ON m.persona_id=i.miembro_afectado_id
        LEFT JOIN persona r ON r.persona_id=i.reemplazo_propuesto_id
        WHERE i.proyecto_id=%s ORDER BY i.fecha_reporte DESC
        """,
        (project_id,),
    )
    project["historial"] = fetch_all(
        """
        SELECT h.*, CONCAT(p.nombres,' ',p.apellidos) AS cambiado_por_nombre
        FROM rsu_workflow_history h
        LEFT JOIN persona p ON p.persona_id=h.cambiado_por
        WHERE h.proyecto_id=%s ORDER BY h.fecha_cambio DESC
        """,
        (project_id,),
    )
    project["certificados"] = fetch_all(
        """
        SELECT c.certificado_id, c.persona_id, CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo,
               c.horas_certificadas, c.fecha_emision, c.codigo_verificacion, ce.nombre AS estado
        FROM certificado c
        INNER JOIN persona p ON p.persona_id=c.persona_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=c.estado_id
        WHERE c.proyecto_id=%s ORDER BY p.apellidos,p.nombres
        """,
        (project_id,),
    )
    review_annex = 7 if str(project.get("etapa_actual") or "") == "evaluacion_final" else 4
    review_assignment = _review_assignment_for_user(project_id, user, review_annex)
    is_representative = _responsible_id(project_id) == user.persona_id
    project["permisos"] = {
        "es_admin": _is_admin(user),
        "es_representante": is_representative,
        "es_revisor_asignado": review_assignment is not None,
        "rol_revisor": (review_assignment or {}).get("rol_revisor"),
        "estado_revision": (review_assignment or {}).get("estado"),
        "anexo_revision": (review_assignment or {}).get("anexo_numero"),
        "es_vicepresidente": str(user.rol_sistema or "").lower() == "vicepresidente_academico",
        "es_director_facultad": str(user.rol_sistema or "").lower() == "director_facultad",
        "puede_editar": is_representative and str(project.get("estado_flujo")) in {"con_observaciones", "observacion_final"},
        "puede_revisar": bool(review_assignment and str(review_assignment.get("estado")) == "pendiente"),
    }
    return project


def _notify_responsible(project_id: str, message: str, event_type: str) -> None:
    row = fetch_one(
        """
        SELECT u.usuario_id FROM rol_proyecto rp
        INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        INNER JOIN usuario u ON u.persona_id=rp.persona_id AND u.activo=1
        WHERE rp.proyecto_id=%s AND LOWER(cr.nombre)='responsable' LIMIT 1
        """,
        (project_id,),
    )
    if row:
        # El envío se ejecuta desde endpoints async mediante ws_manager.send_to_user.
        return


def _applicant_profile_values(user: AuthUser) -> tuple[str, str]:
    """Obtiene domicilio y condición desde el perfil institucional.

    El domicilio del perfil tiene prioridad. Cuando no existe, se permite que
    el formulario lo solicite. La condición docente también se toma del perfil;
    para estudiantes se fija siempre como Estudiante.
    """
    row = fetch_one(
        """
        SELECT c.direccion, d.condicion, a.cargo
        FROM persona p
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        WHERE p.persona_id=%s LIMIT 1
        """,
        (user.persona_id,),
    ) or {}
    address = str(row.get("direccion") or user.domicilio or "").strip()
    user_type = user.tipo_usuario.strip().lower()
    if user_type == "estudiante":
        condition = "Estudiante"
    elif user_type == "docente":
        condition = str(row.get("condicion") or user.condicion or "").strip()
    else:
        condition = str(user.condicion or row.get("cargo") or user.tipo_usuario or "").strip()
    return address, condition


def _validate_applicant_condition(user: AuthUser, value: str) -> None:
    user_type = user.tipo_usuario.strip().lower()
    normalized = value.strip().lower()
    if user_type == "docente" and normalized not in {"ordinario", "contratado"}:
        raise HTTPException(
            status_code=400,
            detail="La condición docente debe estar registrada como Ordinario o Contratado en el perfil, o seleccionarse al completar el anexo.",
        )
    if user_type == "estudiante" and normalized != "estudiante":
        raise HTTPException(status_code=400, detail="La condición del solicitante estudiante debe registrarse como Estudiante.")


def _validate_initial_team(user: AuthUser, members: list[Any]) -> None:
    leader_type = user.tipo_usuario.strip().lower()
    if leader_type not in {"docente", "estudiante"}:
        raise HTTPException(status_code=403, detail="Solo docentes y estudiantes pueden presentar proyectos RSU.")
    if user.persona_id in {item.persona_id for item in members}:
        raise HTTPException(status_code=400, detail="El responsable no debe repetirse dentro del equipo.")
    collaborator_count = sum(1 for item in members if item.role == "colaborador")
    allowed_collaborator_types = {"docente", "estudiante", "no_docente", "egresado", "graduado", "administrativo", "persona"}
    if leader_type == "estudiante":
        if collaborator_count > 7:
            raise HTTPException(status_code=400, detail="Un proyecto liderado por un estudiante admite hasta siete colaboradores.")
        if any(item.role == "corresponsable" for item in members):
            raise HTTPException(status_code=400, detail="Un proyecto liderado por estudiantes no utiliza corresponsables.")
    else:
        if collaborator_count > 5:
            raise HTTPException(status_code=400, detail="Un proyecto liderado por un docente admite hasta cinco colaboradores.")
        if sum(1 for item in members if item.role == "corresponsable") > 2:
            raise HTTPException(status_code=400, detail="Un proyecto docente admite hasta dos corresponsables docentes.")
    for item in members:
        kind = _person_kind(item.persona_id)
        if kind not in allowed_collaborator_types:
            raise HTTPException(status_code=400, detail="El tipo de vínculo de uno de los integrantes no está habilitado como colaborador.")
        if item.role == "corresponsable" and kind != "docente":
            raise HTTPException(status_code=400, detail="Los corresponsables deben ser docentes.")
        if kind == "docente" and item.horas_semanales is None:
            raise HTTPException(status_code=400, detail="Registra entre 2 y 5 horas semanales para cada docente del equipo.")
        if kind != "docente" and item.horas_semanales is not None:
            raise HTTPException(status_code=400, detail="Las horas semanales solo corresponden a participantes docentes.")


@router.get("/catalogo/criterios")
def criteria_catalog(_: AuthUser = Depends(get_current_user)):
    return {"anexo_4": ANNEX4_CRITERIA, "anexo_7": ANNEX7_CRITERIA}


@router.get("/proyectos")
def list_lifecycle_projects(user: AuthUser = Depends(get_current_user)):
    role = str(user.rol_sistema or "").lower()
    if _is_admin(user):
        where = "1=1"
        params: tuple[Any, ...] = ()
    elif role == "director_facultad":
        where = "EXISTS (SELECT 1 FROM rsu_project_review_assignment ra WHERE ra.proyecto_id=p.proyecto_id AND ra.revisor_persona_id=%s AND ra.activo=1)"
        params = (user.persona_id,)
    elif role == "vicepresidente_academico":
        where = "EXISTS (SELECT 1 FROM rsu_project_memorandum mm WHERE mm.proyecto_id=p.proyecto_id AND mm.vicepresidente_persona_id=%s)"
        params = (user.persona_id,)
    else:
        where = "EXISTS (SELECT 1 FROM rol_proyecto rp WHERE rp.proyecto_id=p.proyecto_id AND rp.persona_id=%s AND rp.activo=1)"
        params = (user.persona_id,)
    rows = fetch_all(
        f"""
        SELECT p.proyecto_id, p.titulo, la.nombre AS linea_accion, pa.nombre AS periodo_academico,
               ce.nombre AS estado_proyecto, wf.etapa_actual, wf.estado_flujo, wf.fecha_limite_actual,
               wf.fecha_semana_9, wf.fecha_semana_16,
               wf.anexo5_habilitado_manual, wf.anexo6_habilitado_manual, wf.eliminado_logico,
               CONCAT(pe.nombres,' ',pe.apellidos) AS responsable, rr.persona_id AS responsable_persona_id,
               COALESCE((
                   SELECT COUNT(*)
                   FROM rol_proyecto rp_count
                   WHERE rp_count.proyecto_id=p.proyecto_id AND rp_count.activo=1
               ), 0) AS participantes,
               p.fecha_presentacion, p.fecha_inicio, p.fecha_fin
        FROM proyecto p
        INNER JOIN linea_accion la ON la.linea_accion_id=p.linea_accion_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=p.periodo_academico_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=p.estado_id
        INNER JOIN rsu_project_workflow wf ON wf.proyecto_id=p.proyecto_id
        INNER JOIN rol_proyecto rr ON rr.proyecto_id=p.proyecto_id AND rr.activo=1
        INNER JOIN catalogo_rol cr ON cr.rol_id=rr.rol_id AND LOWER(cr.nombre)='responsable'
        INNER JOIN persona pe ON pe.persona_id=rr.persona_id
        WHERE {where} AND wf.eliminado_logico=0
        ORDER BY wf.actualizado_en DESC, p.fecha_creacion DESC
        """,
        params,
    )
    for row in rows:
        _expire_if_needed(row)
        responsible_person_id = str(row.pop("responsable_persona_id", "") or "")
        row["facultad"] = _person_faculty(responsible_person_id) if responsible_person_id else ""
    return rows


@router.post("/proyectos")
async def create_lifecycle_project(data: Annex12In, user: AuthUser = Depends(get_current_user)):
    _validate_initial_team(user, data.miembros)
    profile_address, profile_condition = _applicant_profile_values(user)
    applicant_address = profile_address or str(data.domicilio_solicitante or "").strip()
    applicant_condition = profile_condition or str(data.condicion_solicitante or "").strip()
    if not applicant_address:
        raise HTTPException(status_code=400, detail="El perfil no tiene domicilio. Ingresa el domicilio en el Anexo 1.")
    _validate_applicant_condition(user, applicant_condition)
    applicant_faculty = _person_faculty(user.persona_id)
    if not (_authority_by_role("director_rsu") or _authority_by_role("administrador_rsu")):
        raise HTTPException(status_code=400, detail="Debe existir un administrador o director de RSU con cuenta activa para revisar el proyecto.")
    if not applicant_faculty or not _authority_by_role("director_facultad", applicant_faculty):
        raise HTTPException(
            status_code=400,
            detail=f"No existe un Director de Facultad activo para {applicant_faculty or 'la facultad del proyecto'}.",
        )
    call = fetch_one(
        """
        SELECT cv.*, ce.nombre AS estado, pa.nombre AS periodo_academico, tp.nombre AS tipo_proyecto
        FROM convocatoria cv
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id=cv.estado_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id=cv.periodo_academico_id
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id=cv.tipo_proyecto_id
        WHERE cv.convocatoria_id=%s
        """,
        (data.convocatoria_id,),
    )
    if call is None or str(call["estado"]).lower() != "abierta":
        raise HTTPException(status_code=400, detail="La convocatoria seleccionada no está abierta.")
    deadline = call["fecha_limite_presentacion"]
    deadline_date = deadline if isinstance(deadline, date) else date.fromisoformat(str(deadline))
    if date.today() > deadline_date:
        raise HTTPException(status_code=400, detail="La convocatoria ya terminó.")
    project_id = str(uuid4())
    responsible_role = _role_id("responsable")
    corresponsible_role = _role_id("corresponsable")
    collaborator_role = _role_id("colaborador")
    state_presented = _state_id("proyecto", "presentado")
    schedule = [item.model_dump() for item in data.cronograma]
    budget = [item.model_dump() for item in data.presupuesto]
    annex1 = {
        "solicitud": "Aprobación de proyecto de RSU",
        "responsable": user.nombre_completo,
        "dni": user.dni,
        "domicilio": applicant_address,
        "condicion": applicant_condition,
        "periodo": call["periodo_academico"],
        "titulo": data.titulo,
        "linea_accion_id": data.linea_accion_id,
    }
    annex2 = data.model_dump(exclude={"firmas"})
    annex2["domicilio_solicitante"] = applicant_address
    annex2["condicion_solicitante"] = applicant_condition
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO proyecto
                (proyecto_id, convocatoria_id, periodo_academico_id, linea_accion_id, titulo,
                 tipo_liderazgo, financiado, estado_id, lugar_ejecucion, poblacion_objetivo,
                 resumen, fecha_inicio, fecha_fin, fecha_presentacion)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                """,
                (project_id, data.convocatoria_id, call["periodo_academico_id"], data.linea_accion_id,
                 data.titulo, user.tipo_usuario.lower(), int(data.financiado), state_presented,
                 data.lugar_ejecucion, data.poblacion_objetivo, data.resumen, data.fecha_inicio, data.fecha_fin),
            )
            cur.execute(
                """
                INSERT INTO proyecto_formulario
                (proyecto_id, solicitud_tipo, domicilio_solicitante, condicion_solicitante,
                 problematica, objetivo, metodologia, impacto_social, impacto_estudiantes,
                 cronograma_json, presupuesto_json, revision_actual)
                VALUES (%s,'Proyecto de RSU',%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                """,
                (project_id, applicant_address, applicant_condition, data.problematica,
                 data.objetivos, data.metodologia, data.impacto_social, data.impacto_estudiantes,
                 json.dumps(schedule, ensure_ascii=False), json.dumps(budget, ensure_ascii=False)),
            )
            cur.execute(
                """
                INSERT INTO rol_proyecto
                (rol_proyecto_id, persona_id, proyecto_id, rol_id, horas_semanales, fecha_asignacion, activo)
                VALUES (%s,%s,%s,%s,%s,CURDATE(),1)
                """,
                (str(uuid4()), user.persona_id, project_id, responsible_role, 2 if user.tipo_usuario.lower() == "docente" else None),
            )
            for member in data.miembros:
                role_id = corresponsible_role if member.role == "corresponsable" else collaborator_role
                cur.execute(
                    """
                    INSERT INTO rol_proyecto
                    (rol_proyecto_id, persona_id, proyecto_id, rol_id, horas_semanales, fecha_asignacion, activo)
                    VALUES (%s,%s,%s,%s,%s,CURDATE(),1)
                    """,
                    (str(uuid4()), member.persona_id, project_id, role_id, member.horas_semanales),
                )
            for item in data.presupuesto:
                total = item.cantidad * item.costo_unitario
                cur.execute(
                    """
                    INSERT INTO financiamiento
                    (financiamiento_id, proyecto_id, partida, descripcion, unidad_medida, cantidad,
                     costo_unitario, monto_total, aprobado)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0)
                    """,
                    (str(uuid4()), project_id, item.partida, item.bien_servicio, item.unidad_medida,
                     item.cantidad, item.costo_unitario, total),
                )
            cur.execute(
                """
                INSERT INTO rsu_project_workflow
                (proyecto_id, etapa_actual, estado_flujo, fecha_primera_presentacion)
                VALUES (%s,'evaluacion_inicial','en_evaluacion',NOW())
                """,
                (project_id,),
            )
            _save_annex(cur, project_id, 1, user.persona_id, annex1)
            _save_annex(cur, project_id, 2, user.persona_id, annex2)
            annex3 = _automatic_annex3_content(
                title=data.titulo,
                line_action_id=data.linea_accion_id,
                responsible_person_id=user.persona_id,
                members=data.miembros,
            )
            annex3_revision = _save_annex(cur, project_id, 3, user.persona_id, annex3)
            _history(cur, project_id, None, "evaluacion_inicial", None, "en_evaluacion", user.persona_id, "Anexos 1 y 2 presentados; Anexo 3 generado automáticamente")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    representative_signatures = [
        item for item in data.firmas
        if item.person_id == user.persona_id or "representante" in item.label.lower()
    ]
    _store_files(project_id, 1, "firma", representative_signatures, user.persona_id)
    _store_files(project_id, 2, "firma", data.firmas, user.persona_id)
    review_cycle = _review_revision(project_id) + 1
    reviewers = _assign_reviewers(project_id, review_cycle)
    reviewer_users = []
    for assigned in reviewers:
        if assigned.get("revisor_persona_id"):
            row = fetch_one("SELECT usuario_id FROM usuario WHERE persona_id=%s AND activo=1", (assigned["revisor_persona_id"],))
            if row:
                reviewer_users.append(str(row["usuario_id"]))
    await ws_manager.broadcast({"type": "ciclo.proyecto_presentado", "audience": ["administrativo"], "message": f"Nuevo proyecto presentado: {data.titulo}", "data": {"proyecto_id": project_id}})
    await notify_admins(
        notification_type="anexos_presentados",
        title="Nuevo proyecto enviado",
        message=f"{user.nombre_completo} presentó los Anexos 1 y 2 del proyecto «{data.titulo}». El Anexo 3 se generó automáticamente y el expediente quedó listo para evaluación.",
        module="Módulo de Anexos",
        priority="alta",
        entity_type="proyecto",
        entity_id=project_id,
        project_id=project_id,
        annex_number=2,
        data={"proyecto_id": project_id, "titulo_proyecto": data.titulo, "anexo_numero": 2, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:anexos12:presentados",
        generated_by=user.nombre_completo,
        important=True,
    )
    await notify_users(
        reviewer_users,
        notification_type="proyecto_pendiente_revision",
        title="Proyecto asignado para revisión",
        message=f"Los Anexos 1, 2 y 3 del proyecto «{data.titulo}» están disponibles. El Anexo 3 fue generado automáticamente.",
        module="Módulo de Evaluación",
        priority="alta",
        entity_type="proyecto",
        entity_id=project_id,
        project_id=project_id,
        annex_number=4,
        data={"proyecto_id": project_id, "anexo_numero": 4, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:revision:{review_cycle}:asignada",
        generated_by=user.nombre_completo,
        important=True,
    )
    return _serialize(project_id, user)


@router.put("/proyectos/{project_id}/anexos-1-2")
async def correct_annexes_1_2(project_id: str, data: Annex12In, user: AuthUser = Depends(get_current_user)):
    project = _assert_access(project_id, user, responsible_only=True)
    if str(project.get("estado_flujo")) != "con_observaciones":
        raise HTTPException(status_code=400, detail="El proyecto no se encuentra en subsanación inicial.")
    if project.get("eliminado_logico"):
        raise HTTPException(status_code=400, detail="El proyecto fue archivado por vencimiento del plazo.")
    _validate_initial_team(user, data.miembros)
    profile_address, profile_condition = _applicant_profile_values(user)
    applicant_address = profile_address or str(data.domicilio_solicitante or "").strip()
    applicant_condition = profile_condition or str(data.condicion_solicitante or "").strip()
    if not applicant_address:
        raise HTTPException(status_code=400, detail="El perfil no tiene domicilio. Ingresa el domicilio en el Anexo 1.")
    _validate_applicant_condition(user, applicant_condition)
    responsible_role = _role_id("responsable")
    corresponsible_role = _role_id("corresponsable")
    collaborator_role = _role_id("colaborador")
    annex1 = {
        "solicitud": "Aprobación de proyecto de RSU", "responsable": user.nombre_completo,
        "dni": user.dni, "domicilio": applicant_address, "condicion": applicant_condition,
        "titulo": data.titulo, "linea_accion_id": data.linea_accion_id,
    }
    annex2 = data.model_dump(exclude={"firmas"})
    annex2["domicilio_solicitante"] = applicant_address
    annex2["condicion_solicitante"] = applicant_condition
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE proyecto SET linea_accion_id=%s,titulo=%s,financiado=%s,lugar_ejecucion=%s,
                    poblacion_objetivo=%s,resumen=%s,fecha_inicio=%s,fecha_fin=%s,fecha_presentacion=NOW()
                WHERE proyecto_id=%s
                """,
                (data.linea_accion_id, data.titulo, int(data.financiado), data.lugar_ejecucion,
                 data.poblacion_objetivo, data.resumen, data.fecha_inicio, data.fecha_fin, project_id),
            )
            cur.execute(
                """
                UPDATE proyecto_formulario SET domicilio_solicitante=%s,condicion_solicitante=%s,
                    problematica=%s,objetivo=%s,metodologia=%s,impacto_social=%s,impacto_estudiantes=%s,
                    cronograma_json=%s,presupuesto_json=%s,revision_actual=revision_actual+1
                WHERE proyecto_id=%s
                """,
                (applicant_address, applicant_condition, data.problematica, data.objetivos,
                 data.metodologia, data.impacto_social, data.impacto_estudiantes,
                 json.dumps([x.model_dump() for x in data.cronograma], ensure_ascii=False),
                 json.dumps([x.model_dump() for x in data.presupuesto], ensure_ascii=False), project_id),
            )
            cur.execute("DELETE FROM rol_proyecto WHERE proyecto_id=%s AND rol_id<>%s", (project_id, responsible_role))
            for member in data.miembros:
                role_id = corresponsible_role if member.role == "corresponsable" else collaborator_role
                cur.execute(
                    """INSERT INTO rol_proyecto
                    (rol_proyecto_id,persona_id,proyecto_id,rol_id,horas_semanales,fecha_asignacion,activo)
                    VALUES (%s,%s,%s,%s,%s,CURDATE(),1)""",
                    (str(uuid4()), member.persona_id, project_id, role_id, member.horas_semanales),
                )
            cur.execute("DELETE FROM financiamiento WHERE proyecto_id=%s", (project_id,))
            for item in data.presupuesto:
                cur.execute(
                    """INSERT INTO financiamiento
                    (financiamiento_id,proyecto_id,partida,descripcion,unidad_medida,cantidad,costo_unitario,monto_total,aprobado)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0)""",
                    (str(uuid4()), project_id, item.partida, item.bien_servicio, item.unidad_medida,
                     item.cantidad, item.costo_unitario, item.cantidad * item.costo_unitario),
                )
            _save_annex(cur, project_id, 1, user.persona_id, annex1)
            _save_annex(cur, project_id, 2, user.persona_id, annex2)
            annex3 = _automatic_annex3_content(
                title=data.titulo,
                line_action_id=data.linea_accion_id,
                responsible_person_id=user.persona_id,
                members=data.miembros,
            )
            annex3_revision = _save_annex(cur, project_id, 3, user.persona_id, annex3)
            _set_workflow(cur, project_id, "evaluacion_inicial", "en_evaluacion", user.persona_id, None, "Anexos 1 y 2 subsanados; Anexo 3 regenerado automáticamente")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    representative_signatures = [
        item for item in data.firmas
        if item.person_id == user.persona_id or "representante" in item.label.lower()
    ]
    _store_files(project_id, 1, "firma", representative_signatures, user.persona_id)
    _store_files(project_id, 2, "firma", data.firmas, user.persona_id)
    review_cycle = _review_revision(project_id) + 1
    reviewers = _assign_reviewers(project_id, review_cycle)
    reviewer_users = []
    for assigned in reviewers:
        if assigned.get("revisor_persona_id"):
            row = fetch_one("SELECT usuario_id FROM usuario WHERE persona_id=%s AND activo=1", (assigned["revisor_persona_id"],))
            if row:
                reviewer_users.append(str(row["usuario_id"]))
    await ws_manager.broadcast({"type": "ciclo.proyecto_subsanado", "audience": ["administrativo"], "message": f"Proyecto subsanado: {data.titulo}", "data": {"proyecto_id": project_id}})
    await notify_admins(
        notification_type="anexos_subsanados",
        title="Corrección de anexos recibida",
        message=f"El responsable corrigió los Anexos 1 y 2 del proyecto «{data.titulo}» y el Anexo 3 se regeneró automáticamente.",
        module="Módulo de Evaluación",
        priority="alta",
        entity_type="proyecto",
        entity_id=project_id,
        project_id=project_id,
        annex_number=2,
        data={"proyecto_id": project_id, "titulo_proyecto": data.titulo, "anexo_numero": 2, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:anexos12:subsanados:{datetime.now().strftime('%Y%m%d%H%M%S')}",
        generated_by=user.nombre_completo,
        important=True,
    )
    await notify_users(
        reviewer_users,
        notification_type="proyecto_subsanado_revision",
        title="Proyecto corregido para nueva revisión",
        message=f"Los Anexos 1, 2 y 3 corregidos del proyecto «{data.titulo}» están disponibles para revisión.",
        module="Módulo de Evaluación",
        priority="alta",
        entity_type="proyecto",
        entity_id=project_id,
        project_id=project_id,
        annex_number=4,
        data={"proyecto_id": project_id, "anexo_numero": 4, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:revision:{review_cycle}:reasignada",
        generated_by=user.nombre_completo,
        important=True,
    )
    return _serialize(project_id, user)


@router.get("/proyectos/{project_id}")
def project_workflow_detail(project_id: str, user: AuthUser = Depends(get_current_user)):
    return _serialize(project_id, user)


@router.post("/proyectos/{project_id}/anexo-3")
async def submit_annex3(project_id: str, data: Annex3In, user: AuthUser = Depends(get_current_user)):
    project = _assert_access(project_id, user, responsible_only=True)
    if str(project.get("etapa_actual")) != "registro_anexo3":
        raise HTTPException(status_code=400, detail="El Anexo 3 no corresponde a la etapa actual.")
    current_team = _team_details(project_id)
    current_ids = {item["persona_id"] for item in current_team}
    submitted_ids = {item.persona_id for item in data.miembros}
    if current_ids != submitted_ids:
        raise HTTPException(status_code=400, detail="El Anexo 3 debe registrar a todo el equipo presentado en los Anexos 1 y 2.")
    responsible_id = _responsible_id(project_id)
    submitted_responsible = next((item.persona_id for item in data.miembros if item.role == "responsable"), None)
    if submitted_responsible != responsible_id or responsible_id != user.persona_id:
        raise HTTPException(status_code=400, detail="El representante que presentó los Anexos 1 y 2 debe completar y presentar el Anexo 3.")
    leadership = str(project.get("tipo_liderazgo") or "").lower()
    collaborator_count = sum(1 for item in data.miembros if item.role == "colaborador")
    corresponsible_count = sum(1 for item in data.miembros if item.role == "corresponsable")
    if leadership == "estudiante":
        if collaborator_count > 7:
            raise HTTPException(status_code=400, detail="El proyecto estudiantil admite hasta siete colaboradores.")
        if corresponsible_count:
            raise HTTPException(status_code=400, detail="Los proyectos liderados por estudiantes no registran corresponsables.")
    else:
        if collaborator_count > 5:
            raise HTTPException(status_code=400, detail="El proyecto docente admite hasta cinco colaboradores.")
        if corresponsible_count > 2:
            raise HTTPException(status_code=400, detail="El proyecto docente admite hasta dos corresponsables docentes.")
    for item in data.miembros:
        if item.role == "corresponsable" and _person_kind(item.persona_id) != "docente":
            raise HTTPException(status_code=400, detail="El rol de corresponsable solo puede asignarse a docentes.")
    faculty = _project_faculty(project_id)
    if not (_authority_by_role("director_rsu") or _authority_by_role("administrador_rsu")):
        raise HTTPException(status_code=400, detail="Debe existir un administrador o director de RSU con cuenta activa para revisar el proyecto.")
    if not faculty or not _authority_by_role("director_facultad", faculty):
        raise HTTPException(status_code=400, detail=f"No existe un director de facultad activo para la facultad del proyecto: {faculty or 'sin facultad identificada' }.")
    content = data.model_dump(exclude={"firma_responsable", "firmas"})
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("UPDATE proyecto SET titulo=%s,linea_accion_id=%s WHERE proyecto_id=%s", (data.titulo, data.linea_accion_id, project_id))
            for member in data.miembros:
                cur.execute("UPDATE rol_proyecto SET rol_id=%s WHERE proyecto_id=%s AND persona_id=%s", (_role_id(member.role), project_id, member.persona_id))
            revision = _save_annex(cur, project_id, 3, user.persona_id, content, "presentado")
            _set_workflow(cur, project_id, "evaluacion_inicial", "en_evaluacion", user.persona_id, None, "Anexo 3 presentado por el responsable")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    if data.firma_responsable:
        _store_file(project_id, 3, "firma_responsable", data.firma_responsable, user.persona_id)
    if data.firmas:
        _store_files(project_id, 3, "firma", data.firmas, user.persona_id)
    review_cycle = _review_revision(project_id) + 1
    reviewers = _assign_reviewers(project_id, review_cycle)
    reviewer_users = []
    for reviewer in reviewers:
        if reviewer.get("revisor_persona_id"):
            row = fetch_one("SELECT usuario_id FROM usuario WHERE persona_id=%s AND activo=1", (reviewer["revisor_persona_id"],))
            if row:
                reviewer_users.append(str(row["usuario_id"]))
    await notify_users(
        reviewer_users,
        notification_type="proyecto_pendiente_revision",
        title="Proyecto asignado para revisión",
        message=f"El responsable presentó los Anexos 1, 2 y 3 del proyecto «{data.titulo}». Debe revisarse mediante el Anexo 4.",
        module="Módulo de Evaluación",
        priority="alta",
        entity_type="proyecto",
        entity_id=project_id,
        project_id=project_id,
        annex_number=4,
        data={"proyecto_id": project_id, "anexo_numero": 4, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:revision:{review_cycle}:asignada",
        generated_by=user.nombre_completo,
        important=True,
    )
    return _serialize(project_id, user)


@router.post("/proyectos/{project_id}/anexo-4/evaluar")
async def evaluate_annex4(project_id: str, data: AnnexEvaluationIn, reviewer: AuthUser = Depends(require_project_reviewer)):
    project = _assert_access(project_id, reviewer)
    if str(project.get("etapa_actual")) != "evaluacion_inicial":
        raise HTTPException(status_code=400, detail="El proyecto no está pendiente de evaluación inicial.")
    assignment = _review_assignment_for_user(project_id, reviewer, 4)
    if not assignment or str(assignment.get("estado")) != "pendiente":
        raise HTTPException(status_code=403, detail="No tienes una revisión pendiente asignada para este proyecto.")
    if assignment.get("rol_revisor") == "director_facultad":
        project_faculty = _project_faculty(project_id)
        if str(assignment.get("facultad") or "").strip().lower() != project_faculty.strip().lower():
            raise HTTPException(status_code=403, detail="La facultad asignada al revisor no coincide con la facultad del proyecto.")
    _validate_annex4_authorities(project_id)
    missing = set(ANNEX4_CRITERIA) - set(data.criterios)
    if missing:
        raise HTTPException(status_code=400, detail="Faltan criterios del Anexo 4: " + ", ".join(sorted(missing)))
    has_observations = bool(
        (data.observaciones or "").strip()
        or any(str(value or "").strip() for value in data.observaciones_por_criterio.values())
    )
    approved = all(bool(data.criterios[key]) for key in ANNEX4_CRITERIA) and not has_observations
    result = "aprobado" if approved else "con_observaciones"
    deadline = None if approved else _add_business_days(date.today(), 3)
    evaluation_id = str(uuid4())
    revision = int(assignment.get("revision") or 1)
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            annex_revision = _save_annex(cur, project_id, 4, reviewer.persona_id, data.model_dump(exclude={"firmas"}), "aprobado" if approved else "observado")
            cur.execute(
                """INSERT INTO rsu_annex_evaluation
                (evaluation_id,proyecto_id,anexo_numero,revision,evaluador_persona_id,criterios_json,
                 total_si,total_no,resultado,observaciones,fecha_limite_subsanacion,rol_revisor,facultad_revisor)
                VALUES (%s,%s,4,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (evaluation_id, project_id, annex_revision, reviewer.persona_id,
                 json.dumps({**data.criterios, "__observaciones_por_criterio": data.observaciones_por_criterio}, ensure_ascii=False),
                 sum(bool(x) for x in data.criterios.values()), sum(not bool(x) for x in data.criterios.values()),
                 result, data.observaciones, deadline, assignment.get("rol_revisor"), assignment.get("facultad")),
            )
            cur.execute(
                """UPDATE rsu_project_review_assignment
                SET estado=%s,evaluation_id=%s,fecha_revision=NOW(),
                    revisor_persona_id=COALESCE(revisor_persona_id,%s)
                WHERE assignment_id=%s AND estado='pendiente'""",
                (result,evaluation_id,reviewer.persona_id,assignment["assignment_id"]),
            )
            if not approved:
                cur.execute("UPDATE rsu_project_review_assignment SET activo=0 WHERE proyecto_id=%s AND anexo_numero=4 AND revision=%s", (project_id,revision))
                _set_workflow(cur, project_id, "subsanacion_inicial", "con_observaciones", reviewer.persona_id, deadline, "Proyecto observado durante la revisión colegiada del Anexo 4")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    if not approved:
        await notify_project_responsibles(
            project_id,
            notification_type="anexo_observado",
            title="Observación registrada",
            message=f"El {('Director de Facultad' if assignment.get('rol_revisor') == 'director_facultad' else 'administrador o director de RSU')} registró observaciones en el Anexo 4 del proyecto «{project['titulo']}». Debes subsanarlas hasta el {deadline}.",
            module="Módulo de Evaluación",
            priority="alta",
            entity_type="anexo",
            entity_id=project_id,
            annex_number=4,
            data={"proyecto_id": project_id, "anexo_numero": 4, "observaciones": data.observaciones, "observaciones_por_criterio": data.observaciones_por_criterio, "fecha_limite": deadline, "pantalla": "proyectos"},
            event_key=f"proyecto:{project_id}:revision:{revision}:observado:{reviewer.persona_id}",
            generated_by=reviewer.nombre_completo,
            important=True,
        )
        return _serialize(project_id, reviewer)

    current_assignments = fetch_all(
        "SELECT * FROM rsu_project_review_assignment WHERE proyecto_id=%s AND anexo_numero=4 AND revision=%s AND activo=1",
        (project_id,revision),
    )
    both_approved = len(current_assignments) == 2 and all(str(item.get("estado")) == "aprobado" for item in current_assignments)
    if both_approved:
        try:
            _validate_memorandum_prerequisites(project_id)
            await _issue_memorandum(project_id, reviewer)
        except Exception as exc:
            # La evaluación y el memorándum forman una sola decisión funcional.
            # Si el PDF no puede emitirse, se revierte únicamente la aprobación
            # actual para que el revisor pueda intentarlo nuevamente después de
            # corregir la configuración del conversor, sin dejar el flujo bloqueado.
            with get_connection() as conn:
                cur = conn.cursor()
                try:
                    cur.execute(
                        "UPDATE rsu_project_review_assignment "
                        "SET estado='pendiente',evaluation_id=NULL,fecha_revision=NULL "
                        "WHERE assignment_id=%s",
                        (assignment["assignment_id"],),
                    )
                    cur.execute("DELETE FROM rsu_annex_evaluation WHERE evaluation_id=%s", (evaluation_id,))
                    cur.execute(
                        "DELETE FROM rsu_annex_submission "
                        "WHERE proyecto_id=%s AND anexo_numero=4 AND revision=%s AND presentado_por=%s",
                        (project_id, annex_revision, reviewer.persona_id),
                    )
                    cur.execute(
                        "UPDATE rsu_annex_submission SET es_actual=1 "
                        "WHERE submission_id=("
                        "SELECT submission_id FROM ("
                        "SELECT submission_id FROM rsu_annex_submission "
                        "WHERE proyecto_id=%s AND anexo_numero=4 "
                        "ORDER BY revision DESC LIMIT 1"
                        ") AS previous_submission)",
                        (project_id,),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    cur.close()
            if isinstance(exc, HTTPException):
                raise exc
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo emitir el memorándum de aprobación: {exc}",
            ) from exc
    else:
        pending = next((item for item in current_assignments if str(item.get("estado")) == "pendiente"), None)
        if pending:
            notification_kwargs = dict(
                notification_type="segunda_revision_pendiente",
                title="Segunda revisión pendiente",
                message=f"El otro revisor aprobó el proyecto «{project['titulo']}». Falta la revisión restante para emitir el memorándum.",
                module="Módulo de Evaluación",
                priority="alta",
                entity_type="proyecto",
                entity_id=project_id,
                project_id=project_id,
                annex_number=4,
                data={"proyecto_id": project_id, "anexo_numero": 4, "pantalla": "proyectos"},
                event_key=f"proyecto:{project_id}:revision:{revision}:segunda-pendiente",
                generated_by=reviewer.nombre_completo,
                important=True,
            )
            if pending.get("rol_revisor") == "administrador_rsu":
                await notify_admins(**notification_kwargs)
            elif pending.get("revisor_persona_id"):
                user_row = fetch_one(
                    "SELECT usuario_id FROM usuario WHERE persona_id=%s AND activo=1",
                    (pending["revisor_persona_id"],),
                )
                if user_row:
                    await notify_users([str(user_row["usuario_id"])], **notification_kwargs)
    return _serialize(project_id, reviewer)


@router.post("/proyectos/{project_id}/acta-resolutiva")
async def register_resolution(project_id: str, data: ResolutionActIn, admin: AuthUser = Depends(require_admin)):
    project = _assert_access(project_id, admin, admin_only=True)
    if str(project.get("etapa_actual")) != "pendiente_acta":
        raise HTTPException(status_code=400, detail="El proyecto no está pendiente de acto resolutivo.")
    file_id = _store_file(project_id, None, "acta_resolutiva", data.archivo, admin.persona_id)
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO rsu_resolution_act
                (resolution_id,proyecto_id,numero_acta,fecha_acta,vicepresidente_nombre,observaciones,
                 workflow_file_id,registrado_por)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE numero_acta=VALUES(numero_acta),fecha_acta=VALUES(fecha_acta),
                  vicepresidente_nombre=VALUES(vicepresidente_nombre),observaciones=VALUES(observaciones),
                  workflow_file_id=VALUES(workflow_file_id),registrado_por=VALUES(registrado_por),fecha_registro=NOW()
                """,
                (str(uuid4()), project_id, data.numero_acta, data.fecha_acta, data.vicepresidente_nombre,
                 data.observaciones, file_id, admin.persona_id),
            )
            cur.execute("UPDATE rsu_project_workflow SET fecha_acta_resolutiva=NOW() WHERE proyecto_id=%s", (project_id,))
            _set_workflow(cur, project_id, "inicio_ejecucion", "listo_ejecucion", admin.persona_id, None, "Acto resolutivo registrado")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    responsible_user = fetch_one("""
        SELECT u.usuario_id FROM rol_proyecto rp INNER JOIN catalogo_rol cr ON cr.rol_id=rp.rol_id
        INNER JOIN usuario u ON u.persona_id=rp.persona_id AND u.activo=1
        WHERE rp.proyecto_id=%s AND LOWER(cr.nombre)='responsable' LIMIT 1
    """, (project_id,))
    if responsible_user:
        await ws_manager.send_to_user(str(responsible_user["usuario_id"]), {"type": "ciclo.acta_registrada", "message": "El acto resolutivo fue registrado. Ya puedes iniciar la ejecución.", "data": {"proyecto_id": project_id}})
    await notify_project_responsibles(
        project_id,
        notification_type="acta_registrada",
        title="Acto resolutivo registrado",
        message=f"El acto resolutivo del proyecto «{project['titulo']}» fue registrado. Ya puedes iniciar la ejecución.",
        module="Módulo de Proyectos",
        priority="alta",
        entity_type="proyecto",
        entity_id=project_id,
        data={"proyecto_id": project_id, "titulo_proyecto": project["titulo"], "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:acta:{data.numero_acta}",
        generated_by=admin.nombre_completo,
        important=True,
    )
    return _serialize(project_id, admin)


@router.get("/proyectos/{project_id}/memorandum")
def download_memorandum(project_id: str, formato: str = "pdf", user: AuthUser = Depends(get_current_user)):
    _assert_access(project_id, user)
    record = fetch_one("SELECT * FROM rsu_project_memorandum WHERE proyecto_id=%s", (project_id,))
    if not record:
        raise HTTPException(status_code=404, detail="El proyecto todavía no tiene memorándum de aprobación.")
    requested_pdf = str(formato or "pdf").lower() != "docx"
    stored_path = record.get("archivo_pdf_url") if requested_pdf else record.get("archivo_docx_url")
    path = _resolve_database_path(stored_path)
    if requested_pdf and not path.exists():
        raise HTTPException(
            status_code=409,
            detail="El memorándum Word fue emitido, pero no pudo convertirse a PDF. Descárgalo en formato DOCX o configura LibreOffice.",
        )
    if not path.exists():
        raise HTTPException(status_code=404, detail="El archivo del memorándum no está disponible.")
    media = "application/pdf" if path.suffix.lower() == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    project_row = fetch_one("SELECT titulo FROM proyecto WHERE proyecto_id=%s", (project_id,)) or {}
    safe_title = _safe_filename_component(project_row.get("titulo"))
    return FileResponse(path, media_type=media, filename=f"{safe_title}-memorandum_aprobacion{path.suffix.lower()}")


@router.get("/proyectos/{project_id}/memorandum-certificacion")
def download_certificate_memorandum(
    project_id: str,
    formato: str = "pdf",
    user: AuthUser = Depends(get_current_user),
):
    _assert_access(project_id, user)
    record = fetch_one(
        "SELECT * FROM rsu_certificate_memorandum WHERE proyecto_id=%s",
        (project_id,),
    )
    if not record:
        raise HTTPException(status_code=404, detail="El proyecto todavía no tiene memorándum de aprobación final.")
    requested_pdf = str(formato or "pdf").lower() != "docx"
    stored = record.get("archivo_pdf_url") if requested_pdf else record.get("archivo_docx_url")
    path = _resolve_database_path(stored)
    if requested_pdf and not path.exists():
        raise HTTPException(status_code=409, detail="El memorándum de aprobación final no está disponible en PDF.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="El archivo del memorándum de aprobación final no está disponible.")
    media = "application/pdf" if path.suffix.lower() == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    project_row = fetch_one("SELECT titulo FROM proyecto WHERE proyecto_id=%s", (project_id,)) or {}
    safe_title = _safe_filename_component(project_row.get("titulo"))
    return FileResponse(path, media_type=media, filename=f"{safe_title}-memorandum_aprobacion_final{path.suffix.lower()}")


@router.post("/proyectos/{project_id}/iniciar-ejecucion")
async def start_execution(project_id: str, data: StartExecutionIn, user: AuthUser = Depends(get_current_user)):
    project = _assert_access(project_id, user, responsible_only=True)
    if not fetch_one("SELECT 1 FROM rsu_project_memorandum WHERE proyecto_id=%s", (project_id,)):
        raise HTTPException(status_code=400, detail="El proyecto aún no cuenta con el memorándum de aprobación.")
    # La versión anterior confirmaba la transacción antes de emitir el aviso
    # WebSocket. Si el aviso fallaba por una fecha no serializable, el cliente
    # recibía 500 aunque el proyecto ya había iniciado. Repetir la operación
    # ahora devuelve el estado actual sin duplicar fechas ni transiciones.
    if (
        str(project.get("etapa_actual")) == "ejecucion"
        and str(project.get("estado_flujo")) in {"en_ejecucion", "monitoreo"}
    ):
        return _serialize(project_id, user)
    if str(project.get("etapa_actual")) != "inicio_ejecucion":
        raise HTTPException(status_code=400, detail="El proyecto aún no está habilitado para iniciar ejecución.")
    start = data.fecha_inicio or date.today()
    week9 = start + timedelta(weeks=8)
    week16 = start + timedelta(weeks=15)
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute("UPDATE proyecto SET fecha_inicio=%s WHERE proyecto_id=%s", (start, project_id))
            cur.execute(
                """
                UPDATE rsu_project_workflow SET fecha_inicio_ejecucion=%s,fecha_semana_9=%s,fecha_semana_16=%s
                WHERE proyecto_id=%s
                """,
                (start, week9, week16, project_id),
            )
            _set_workflow(cur, project_id, "ejecucion", "en_ejecucion", user.persona_id, week9, "Inicio de ejecución confirmado")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    await ws_manager.broadcast({"type": "ciclo.ejecucion_iniciada", "audience": ["administrativo"], "message": f"Inició la ejecución del proyecto {project['titulo']}", "data": {"proyecto_id": project_id, "fecha_semana_9": week9, "fecha_semana_16": week16}})
    await notify_admins(
        notification_type="ejecucion_iniciada",
        title="Proyecto en ejecución",
        message=f"El proyecto «{project['titulo']}» inició su etapa de ejecución.",
        module="Módulo de Proyectos",
        priority="informativa",
        entity_type="proyecto",
        entity_id=project_id,
        project_id=project_id,
        data={"proyecto_id": project_id, "titulo_proyecto": project["titulo"], "fecha_semana_9": week9, "fecha_semana_16": week16, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:ejecucion:iniciada",
        generated_by=user.nombre_completo,
    )
    return _serialize(project_id, user)


@router.post("/proyectos/{project_id}/incidencias")
async def report_incident(project_id: str, data: IncidentIn, user: AuthUser = Depends(get_current_user)):
    project = _assert_access(project_id, user, responsible_only=True)
    if str(project.get("estado_flujo")) not in {"en_ejecucion", "monitoreo", "cierre"}:
        raise HTTPException(status_code=400, detail="Las incidencias solo se registran durante la ejecución.")
    incident_id = str(uuid4())
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO rsu_project_incident
                (incident_id,proyecto_id,reportado_por,tipo,descripcion,miembro_afectado_id,reemplazo_propuesto_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (incident_id, project_id, user.persona_id, data.tipo, data.descripcion,
                 data.miembro_afectado_id, data.reemplazo_propuesto_id),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    await ws_manager.broadcast({"type": "ciclo.incidencia_reportada", "audience": ["administrativo"], "message": f"Se reportó una incidencia en {project['titulo']}", "data": {"proyecto_id": project_id, "incident_id": incident_id}})
    await notify_admins(
        notification_type="incidencia_reportada",
        title="Incidencia de proyecto",
        message=f"Se reportó una incidencia en el proyecto «{project['titulo']}».",
        module="Módulo de Proyectos",
        priority="alta",
        entity_type="incidencia",
        entity_id=incident_id,
        project_id=project_id,
        data={"proyecto_id": project_id, "titulo_proyecto": project["titulo"], "incident_id": incident_id, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:incidencia:{incident_id}",
        generated_by=user.nombre_completo,
        important=True,
    )
    return _serialize(project_id, user)


@router.post("/proyectos/{project_id}/incidencias/{incident_id}/revisar")
def review_incident(project_id: str, incident_id: str, data: IncidentReviewIn, admin: AuthUser = Depends(require_admin)):
    _assert_access(project_id, admin, admin_only=True)
    incident = fetch_one("SELECT * FROM rsu_project_incident WHERE incident_id=%s AND proyecto_id=%s", (incident_id, project_id))
    if incident is None:
        raise HTTPException(status_code=404, detail="Incidencia no encontrada.")
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE rsu_project_incident SET estado=%s,respuesta_admin=%s,verificado_por=%s,fecha_verificacion=NOW()
                WHERE incident_id=%s
                """,
                (data.resultado, data.respuesta_admin, admin.persona_id, incident_id),
            )
            if data.resultado == "aprobado" and incident.get("miembro_afectado_id"):
                cur.execute("UPDATE rol_proyecto SET activo=0 WHERE proyecto_id=%s AND persona_id=%s", (project_id, incident["miembro_afectado_id"]))
                if incident.get("reemplazo_propuesto_id"):
                    role = fetch_one("SELECT rol_id FROM rol_proyecto WHERE proyecto_id=%s AND persona_id=%s LIMIT 1", (project_id, incident["miembro_afectado_id"]))
                    if role:
                        cur.execute(
                            """INSERT INTO rol_proyecto
                            (rol_proyecto_id,persona_id,proyecto_id,rol_id,fecha_asignacion,activo)
                            VALUES (%s,%s,%s,%s,CURDATE(),1)
                            ON DUPLICATE KEY UPDATE rol_id=VALUES(rol_id),activo=1,fecha_asignacion=CURDATE()""",
                            (str(uuid4()), incident["reemplazo_propuesto_id"], project_id, role["rol_id"]),
                        )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    return _serialize(project_id, admin)


@router.post("/proyectos/{project_id}/habilitar-anexo-5")
async def enable_annex5_now(
    project_id: str,
    data: WorkflowNoteIn,
    user: AuthUser = Depends(get_current_user),
):
    if not _is_administrador_rsu(user):
        raise HTTPException(status_code=403, detail="Solo el perfil administrador_rsu puede habilitar anticipadamente los informes.")
    project = _assert_access(project_id, user, admin_only=True)
    if str(project.get("estado_flujo") or "") not in {"en_ejecucion", "monitoreo"}:
        raise HTTPException(status_code=400, detail="El proyecto debe encontrarse en ejecución para habilitar el Anexo 5.")
    if fetch_one(
        "SELECT 1 FROM rsu_annex_submission WHERE proyecto_id=%s AND anexo_numero=5 ORDER BY revision DESC LIMIT 1",
        (project_id,),
    ):
        raise HTTPException(status_code=400, detail="El Anexo 5 ya fue presentado.")
    if bool(project.get("anexo5_habilitado_manual")):
        return _serialize(project_id, user)
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """UPDATE rsu_project_workflow
                   SET anexo5_habilitado_manual=1, fecha_habilitacion_anexo5=NOW(),
                       anexo5_habilitado_por=%s
                   WHERE proyecto_id=%s""",
                (user.persona_id, project_id),
            )
            _history(
                cur, project_id, str(project.get("etapa_actual") or "ejecucion"),
                str(project.get("etapa_actual") or "ejecucion"),
                str(project.get("estado_flujo") or "en_ejecucion"),
                str(project.get("estado_flujo") or "en_ejecucion"),
                user.persona_id,
                data.comentario or "Administrador RSU habilitó anticipadamente el Anexo 5.",
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    await notify_project_responsibles(
        project_id,
        notification_type="anexo_habilitado",
        title="Anexo 5 habilitado",
        message=f"El administrador de RSU habilitó anticipadamente el informe de avance del proyecto «{project['titulo']}».",
        module="Módulo de Proyectos",
        priority="alta",
        entity_type="anexo",
        entity_id=project_id,
        annex_number=5,
        data={"proyecto_id": project_id, "anexo_numero": 5, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:anexo5:habilitado",
        generated_by=user.nombre_completo,
        important=True,
    )
    return _serialize(project_id, user)


@router.post("/proyectos/{project_id}/habilitar-anexo-6")
async def enable_annex6_now(
    project_id: str,
    data: WorkflowNoteIn,
    user: AuthUser = Depends(get_current_user),
):
    if not _is_administrador_rsu(user):
        raise HTTPException(status_code=403, detail="Solo el perfil administrador_rsu puede habilitar anticipadamente los informes.")
    project = _assert_access(project_id, user, admin_only=True)
    if str(project.get("estado_flujo") or "") != "monitoreo":
        raise HTTPException(status_code=400, detail="Primero debe presentarse el Anexo 5 para habilitar anticipadamente el Anexo 6.")
    if fetch_one(
        "SELECT 1 FROM rsu_annex_submission WHERE proyecto_id=%s AND anexo_numero=6 ORDER BY revision DESC LIMIT 1",
        (project_id,),
    ):
        raise HTTPException(status_code=400, detail="El Anexo 6 ya fue presentado.")
    if bool(project.get("anexo6_habilitado_manual")):
        return _serialize(project_id, user)
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """UPDATE rsu_project_workflow
                   SET anexo6_habilitado_manual=1, fecha_habilitacion_anexo6=NOW(),
                       anexo6_habilitado_por=%s
                   WHERE proyecto_id=%s""",
                (user.persona_id, project_id),
            )
            _history(
                cur, project_id, str(project.get("etapa_actual") or "ejecucion"),
                str(project.get("etapa_actual") or "ejecucion"),
                str(project.get("estado_flujo") or "monitoreo"),
                str(project.get("estado_flujo") or "monitoreo"),
                user.persona_id,
                data.comentario or "Administrador RSU habilitó anticipadamente el Anexo 6.",
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    await notify_project_responsibles(
        project_id,
        notification_type="anexo_habilitado",
        title="Anexo 6 habilitado",
        message=f"El administrador de RSU habilitó anticipadamente el informe final del proyecto «{project['titulo']}».",
        module="Módulo de Proyectos",
        priority="alta",
        entity_type="anexo",
        entity_id=project_id,
        annex_number=6,
        data={"proyecto_id": project_id, "anexo_numero": 6, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:anexo6:habilitado",
        generated_by=user.nombre_completo,
        important=True,
    )
    return _serialize(project_id, user)


@router.post("/proyectos/{project_id}/anexo-5")
async def submit_annex5(project_id: str, data: Annex5In, user: AuthUser = Depends(get_current_user)):
    project = _assert_access(project_id, user, responsible_only=True)
    due = project.get("fecha_semana_9")
    if str(project.get("estado_flujo")) not in {"en_ejecucion", "monitoreo"}:
        raise HTTPException(status_code=400, detail="El proyecto no se encuentra en etapa de monitoreo.")
    manually_enabled = bool(project.get("anexo5_habilitado_manual"))
    if due and not manually_enabled and date.today() < (due if isinstance(due, date) else date.fromisoformat(str(due))):
        raise HTTPException(status_code=400, detail=f"El Anexo 5 se habilita en la semana 9 ({due}) o mediante apertura anticipada del administrador RSU.")
    content = data.model_dump(exclude={"firmas", "evidencias"})
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            _save_annex(cur, project_id, 5, user.persona_id, content, "presentado")
            cur.execute(
                "UPDATE rol_proyecto SET horas_acumuladas=GREATEST(COALESCE(horas_acumuladas,0),%s) "
                "WHERE proyecto_id=%s AND activo=1",
                (data.duracion_horas, project_id),
            )
            _set_workflow(cur, project_id, "ejecucion", "monitoreo", user.persona_id, project.get("fecha_semana_16"), "Anexo 5 presentado")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    _store_files(project_id, 5, "firma", data.firmas, user.persona_id)
    _store_files(project_id, 5, "evidencia", data.evidencias, user.persona_id)
    await ws_manager.broadcast({"type": "ciclo.anexo5_presentado", "audience": ["administrativo"], "message": f"Informe de avance presentado: {project['titulo']}", "data": {"proyecto_id": project_id}})
    await notify_admins(
        notification_type="anexo_presentado",
        title="Anexo 5 enviado",
        message=f"El responsable presentó el informe de avance del proyecto «{project['titulo']}».",
        module="Módulo de Anexos",
        priority="alta",
        entity_type="anexo",
        entity_id=project_id,
        project_id=project_id,
        annex_number=5,
        data={"proyecto_id": project_id, "titulo_proyecto": project["titulo"], "anexo_numero": 5, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:anexo5:presentado:{datetime.now().strftime('%Y%m%d%H%M%S')}",
        generated_by=user.nombre_completo,
        important=True,
    )
    return _serialize(project_id, user)


@router.post("/proyectos/{project_id}/anexo-6")
async def submit_annex6(project_id: str, data: Annex6In, user: AuthUser = Depends(get_current_user)):
    project = _assert_access(project_id, user, responsible_only=True)
    state = str(project.get("estado_flujo"))
    if state not in {"monitoreo", "observacion_final"}:
        raise HTTPException(status_code=400, detail="El informe final no corresponde a la etapa actual.")
    due = project.get("fecha_semana_16")
    manually_enabled = bool(project.get("anexo6_habilitado_manual"))
    if state != "observacion_final" and due and not manually_enabled and date.today() < (due if isinstance(due, date) else date.fromisoformat(str(due))):
        raise HTTPException(status_code=400, detail=f"El Anexo 6 se habilita en la semana 16 ({due}) o mediante apertura anticipada del administrador RSU.")
    if project.get("eliminado_logico"):
        raise HTTPException(status_code=400, detail="El proyecto no admite nuevas presentaciones.")
    content = data.model_dump(exclude={"firmas", "evidencias"})

    # Las semanas 1 a 9 del informe final se precargan desde el Anexo 5.
    # Los valores enviados por el responsable prevalecen, por lo que siguen
    # siendo completamente editables antes de presentar el Anexo 6.
    progress_content = dict(
        (((project.get("anexos") or {}).get("5") or {}).get("contenido") or {})
    )
    merged_activities = {
        key: value
        for key, value in _weekly_activity_dict(
            progress_content.get("actividades_semanales") or {}
        ).items()
        if 1 <= int(key) <= 9
    }
    merged_activities.update(
        _weekly_activity_dict(content.get("actividades_semanales") or {})
    )
    content["actividades_semanales"] = merged_activities

    responsible = next(
        (item for item in _team_details(project_id) if str(item.get("rol") or "").lower() == "responsable"),
        {},
    )
    content["facultad"] = str(responsible.get("facultad") or _project_faculty(project_id) or "Universidad Nacional de Barranca")
    content["escuela_profesional"] = str(responsible.get("escuela") or "No especificada")
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            _save_annex(cur, project_id, 6, user.persona_id, content, "presentado")
            cur.execute(
                "UPDATE rol_proyecto SET horas_acumuladas=%s WHERE proyecto_id=%s AND activo=1",
                (data.duracion_horas, project_id),
            )
            _set_workflow(cur, project_id, "evaluacion_final", "cierre", user.persona_id, None, "Anexo 6 presentado")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()
    _store_files(project_id, 6, "firma", data.firmas, user.persona_id)
    _store_files(project_id, 6, "evidencia", data.evidencias, user.persona_id)
    review_cycle = _review_revision(project_id, 7) + 1
    final_reviewers = _assign_reviewers(project_id, review_cycle, 7)
    director_users: list[str] = []
    for assigned in final_reviewers:
        if assigned.get("rol_revisor") == "director_facultad" and assigned.get("revisor_persona_id"):
            row = fetch_one(
                "SELECT usuario_id FROM usuario WHERE persona_id=%s AND activo=1",
                (assigned["revisor_persona_id"],),
            )
            if row:
                director_users.append(str(row["usuario_id"]))
    if director_users:
        await notify_users(
            director_users,
            notification_type="informe_final_pendiente_revision",
            title="Informe final pendiente de evaluación",
            message=f"El responsable presentó el Anexo 6 del proyecto «{project['titulo']}». Debes participar en su evaluación mediante el Anexo 7.",
            module="Módulo de Evaluación",
            priority="alta",
            entity_type="anexo",
            entity_id=project_id,
            project_id=project_id,
            annex_number=7,
            data={"proyecto_id": project_id, "anexo_numero": 7, "pantalla": "proyectos"},
            event_key=f"proyecto:{project_id}:anexo7:revision:{review_cycle}:director",
            generated_by=user.nombre_completo,
            important=True,
        )
    await ws_manager.broadcast({"type": "ciclo.anexo6_presentado", "audience": ["administrativo"], "message": f"Informe final presentado: {project['titulo']}", "data": {"proyecto_id": project_id}})
    await notify_admins(
        notification_type="anexo_presentado",
        title="Anexo 6 enviado",
        message=f"El responsable presentó el informe final del proyecto «{project['titulo']}».",
        module="Módulo de Anexos",
        priority="alta",
        entity_type="anexo",
        entity_id=project_id,
        project_id=project_id,
        annex_number=6,
        data={"proyecto_id": project_id, "titulo_proyecto": project["titulo"], "anexo_numero": 6, "pantalla": "proyectos"},
        event_key=f"proyecto:{project_id}:anexo6:presentado:{datetime.now().strftime('%Y%m%d%H%M%S')}",
        generated_by=user.nombre_completo,
        important=True,
    )
    return _serialize(project_id, user)


@router.post("/proyectos/{project_id}/anexo-7/evaluar")
async def evaluate_annex7(
    project_id: str,
    data: AnnexEvaluationIn,
    reviewer: AuthUser = Depends(require_project_reviewer),
):
    project = _assert_access(project_id, reviewer)
    if str(project.get("etapa_actual")) != "evaluacion_final":
        raise HTTPException(status_code=400, detail="El informe final no está pendiente de evaluación.")
    assignment = _review_assignment_for_user(project_id, reviewer, 7)
    if not assignment or str(assignment.get("estado")) != "pendiente":
        raise HTTPException(status_code=403, detail="No tienes una evaluación final pendiente para este proyecto.")
    if assignment.get("rol_revisor") == "director_facultad":
        project_faculty = _project_faculty(project_id)
        if str(assignment.get("facultad") or "").strip().casefold() != project_faculty.strip().casefold():
            raise HTTPException(status_code=403, detail="La facultad del Director no coincide con la del proyecto.")
    missing = set(ANNEX7_CRITERIA) - set(data.criterios)
    if missing:
        raise HTTPException(status_code=400, detail="Faltan criterios del Anexo 7: " + ", ".join(sorted(missing)))
    has_observations = bool(
        (data.observaciones or "").strip()
        or any(str(value or "").strip() for value in data.observaciones_por_criterio.values())
    )
    approved = all(bool(data.criterios[key]) for key in ANNEX7_CRITERIA) and not has_observations
    result = "aprobado" if approved else "con_observaciones"
    deadline = None if approved else _add_business_days(date.today(), 3)
    evaluation_id = str(uuid4())
    review_cycle = int(assignment.get("revision") or 1)
    if approved:
        other_approved = fetch_one(
            """SELECT 1 AS ok FROM rsu_project_review_assignment
               WHERE proyecto_id=%s AND anexo_numero=7 AND revision=%s AND activo=1
                 AND assignment_id<>%s AND estado='aprobado' LIMIT 1""",
            (project_id, review_cycle, assignment["assignment_id"]),
        )
        if other_approved:
            _final_memorandum_context(project_id, reviewer)
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            annex_revision = _save_annex(
                cur,
                project_id,
                7,
                reviewer.persona_id,
                data.model_dump(exclude={"firmas"}),
                "aprobado" if approved else "observado",
            )
            cur.execute(
                """INSERT INTO rsu_annex_evaluation
                (evaluation_id,proyecto_id,anexo_numero,revision,evaluador_persona_id,criterios_json,
                 total_si,total_no,resultado,observaciones,fecha_limite_subsanacion,rol_revisor,facultad_revisor)
                VALUES (%s,%s,7,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    evaluation_id,
                    project_id,
                    annex_revision,
                    reviewer.persona_id,
                    json.dumps(
                        {**data.criterios, "__observaciones_por_criterio": data.observaciones_por_criterio},
                        ensure_ascii=False,
                    ),
                    sum(bool(x) for x in data.criterios.values()),
                    sum(not bool(x) for x in data.criterios.values()),
                    result,
                    data.observaciones,
                    deadline,
                    assignment.get("rol_revisor"),
                    assignment.get("facultad"),
                ),
            )
            cur.execute(
                """UPDATE rsu_project_review_assignment
                SET estado=%s,evaluation_id=%s,fecha_revision=NOW(),
                    revisor_persona_id=COALESCE(revisor_persona_id,%s)
                WHERE assignment_id=%s AND estado='pendiente'""",
                (result, evaluation_id, reviewer.persona_id, assignment["assignment_id"]),
            )
            if not approved:
                cur.execute(
                    """UPDATE rsu_project_review_assignment SET activo=0
                    WHERE proyecto_id=%s AND anexo_numero=7 AND revision=%s""",
                    (project_id, review_cycle),
                )
                _set_workflow(
                    cur,
                    project_id,
                    "subsanacion_final",
                    "observacion_final",
                    reviewer.persona_id,
                    deadline,
                    "Informe final observado durante la evaluación colegiada",
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()

    if not approved:
        await notify_project_responsibles(
            project_id,
            notification_type="informe_final_observado",
            title="Informe final observado",
            message=(
                f"El {('Director de Facultad' if assignment.get('rol_revisor') == 'director_facultad' else 'administrador de RSU')} "
                f"registró observaciones en el informe final del proyecto «{project['titulo']}». "
                f"Plazo de subsanación: {deadline}."
            ),
            module="Módulo de Evaluación",
            priority="alta",
            entity_type="anexo",
            entity_id=project_id,
            annex_number=7,
            data={
                "proyecto_id": project_id,
                "titulo_proyecto": project["titulo"],
                "anexo_numero": 7,
                "resultado": result,
                "observaciones": data.observaciones,
                "observaciones_por_criterio": data.observaciones_por_criterio,
                "fecha_limite": deadline,
                "pantalla": "proyectos",
            },
            event_key=f"proyecto:{project_id}:anexo7:revision:{review_cycle}:observado:{reviewer.persona_id}",
            generated_by=reviewer.nombre_completo,
            important=True,
        )
        return _serialize(project_id, reviewer)

    current_assignments = fetch_all(
        """SELECT * FROM rsu_project_review_assignment
        WHERE proyecto_id=%s AND anexo_numero=7 AND revision=%s AND activo=1""",
        (project_id, review_cycle),
    )
    both_approved = len(current_assignments) == 2 and all(
        str(item.get("estado")) == "aprobado" for item in current_assignments
    )
    if both_approved:
        await _issue_final_approval_memorandum(project_id, reviewer)
        with get_connection() as conn:
            cur = conn.cursor()
            try:
                _set_workflow(
                    cur,
                    project_id,
                    "certificacion",
                    "cierre",
                    reviewer.persona_id,
                    None,
                    "Informe final aprobado por Administración RSU y Dirección de Facultad mediante Anexo 7",
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
        await notify_project_responsibles(
            project_id,
            notification_type="informe_final_aprobado",
            title="Informe final aprobado",
            message=f"El informe final del proyecto «{project['titulo']}» fue aprobado por los dos revisores y pasó a certificación.",
            module="Módulo de Evaluación",
            priority="informativa",
            entity_type="anexo",
            entity_id=project_id,
            annex_number=7,
            data={"proyecto_id": project_id, "anexo_numero": 7, "resultado": "aprobado", "pantalla": "proyectos"},
            event_key=f"proyecto:{project_id}:anexo7:revision:{review_cycle}:aprobado",
            generated_by=reviewer.nombre_completo,
        )
    else:
        pending = next((item for item in current_assignments if str(item.get("estado")) == "pendiente"), None)
        if pending:
            kwargs = dict(
                notification_type="segunda_revision_final_pendiente",
                title="Segunda evaluación final pendiente",
                message=f"El otro revisor aprobó el informe final del proyecto «{project['titulo']}». Falta la evaluación restante.",
                module="Módulo de Evaluación",
                priority="alta",
                entity_type="anexo",
                entity_id=project_id,
                project_id=project_id,
                annex_number=7,
                data={"proyecto_id": project_id, "anexo_numero": 7, "pantalla": "proyectos"},
                event_key=f"proyecto:{project_id}:anexo7:revision:{review_cycle}:segunda-pendiente",
                generated_by=reviewer.nombre_completo,
                important=True,
            )
            if pending.get("rol_revisor") == "administrador_rsu":
                await notify_admins(**kwargs)
            elif pending.get("revisor_persona_id"):
                user_row = fetch_one(
                    "SELECT usuario_id FROM usuario WHERE persona_id=%s AND activo=1",
                    (pending["revisor_persona_id"],),
                )
                if user_row:
                    await notify_users([str(user_row["usuario_id"])], **kwargs)
    return _serialize(project_id, reviewer)


@router.post("/proyectos/{project_id}/certificar")
async def issue_certificates(
    project_id: str,
    data: CertificateIssueIn,
    admin: AuthUser = Depends(require_admin),
):
    project = _assert_access(project_id, admin, admin_only=True)
    deny_certificate_for_simulation(project_id)
    team = _team_details(project_id)
    if not team:
        raise HTTPException(status_code=400, detail="El proyecto no tiene participantes activos.")

    stage = str(project.get("etapa_actual") or "")
    if stage == "finalizado":
        existing = fetch_one(
            """SELECT COUNT(DISTINCT persona_id) AS total
               FROM certificado WHERE proyecto_id=%s AND fecha_emision IS NOT NULL""",
            (project_id,),
        )
        if int((existing or {}).get("total") or 0) >= len(team):
            # La primera solicitud pudo terminar correctamente aunque el cliente
            # agotara su tiempo de espera. Una repetición devuelve el resultado
            # ya emitido en vez de informar que el proyecto no está listo.
            result = _serialize(project_id, admin)
            result["certificacion_ya_emitida"] = True
            return result
    elif stage != "certificacion":
        raise HTTPException(status_code=400, detail="El proyecto aún no está listo para certificación.")
    vice = _authority_by_role("vicepresidente_academico")
    director = _authority_by_role("director_rsu") or _authority_by_role("administrador_rsu")
    if not vice:
        raise HTTPException(status_code=400, detail="Registra un Vicepresidente académico activo antes de emitir los certificados.")
    if not director:
        raise HTTPException(status_code=400, detail="Registra un perfil activo para la Dirección de RSU.")

    emitted = data.fecha_emision or date.today()
    state_emitted = _state_id("certificado", "emitido")
    signature_path: Path | None = None
    if data.firma_director is not None:
        signature_extension = Path(data.firma_director.filename).suffix.lower()
        if signature_extension not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise HTTPException(status_code=400, detail="La firma del director debe adjuntarse como imagen PNG, JPG o WEBP.")
        signature_file_id = _store_file(project_id, 14, "firma_director", data.firma_director, admin.persona_id)
        signature_record = fetch_one(
            "SELECT archivo_url FROM rsu_workflow_file WHERE workflow_file_id=%s",
            (signature_file_id,),
        )
        signature_path = _resolve_database_path((signature_record or {}).get("archivo_url"))
    else:
        registered_signature = str(director.get("firma_url") or "") or _director_signature(
            project_id, str(director.get("persona_id") or "")
        )
        if registered_signature:
            signature_path = _resolve_database_path(registered_signature)
    if signature_path is None or not signature_path.is_file():
        raise HTTPException(
            status_code=400,
            detail="No se encontró una firma institucional válida. Regístrala en Roles y autoridades o adjunta una imagen.",
        )

    # El segundo memorándum se emite al aprobar el Anexo 7. Para proyectos
    # anteriores, se recupera o genera aquí de forma idempotente.
    final_memorandum = await _issue_final_approval_memorandum(project_id, admin)

    base = _workflow_storage_root() / "private_rsu_workflow" / project_id / "certificados"
    base.mkdir(parents=True, exist_ok=True)
    prepared: list[dict[str, Any]] = []
    certificate_jobs: list[tuple[Path, dict[str, Any]]] = []
    for person in team:
        current = fetch_one(
            """SELECT certificado_id, codigo_verificacion FROM certificado
            WHERE persona_id=%s AND proyecto_id=%s
            ORDER BY fecha_solicitud DESC LIMIT 1""",
            (person["persona_id"], project_id),
        )
        certificate_id = str(current["certificado_id"]) if current else str(uuid4())
        verification = str((current or {}).get("codigo_verificacion") or "")
        if not verification:
            verification = f"RSU-{datetime.now():%Y%m%d}-{uuid4().hex[:10].upper()}"
        output = base / f"certificado_{person['persona_id']}.pdf"
        identification = (
            f"Código: {person['codigo']}"
            if person["tipo_persona"] == "Estudiante"
            else f"DNI: {person['dni']}"
        )
        certificate_data = {
            "nombre_completo": person["nombre_completo"],
            "identificacion": identification,
            "titulo": project["titulo"],
            "tipo_proyecto": project.get("tipo_proyecto"),
            "linea_accion": project["linea_accion"],
            "horas": data.horas_certificadas,
            "programa_academico": person.get("escuela") or "UNAB",
            "fecha_emision": emitted.strftime("%d/%m/%Y"),
            "firma_director_path": str(signature_path),
            "nombre_director": director.get("nombre_completo"),
        }
        certificate_jobs.append((output, certificate_data))
        prepared.append(
            {
                "person": person,
                "current": bool(current),
                "certificate_id": certificate_id,
                "verification": verification,
                "output": output,
                "database_path": _path_for_database(output),
            }
        )

    generated_paths = {
        item.resolve()
        for item in generate_certificate_pdfs_batch(certificate_jobs)
        if item.exists() and item.stat().st_size > 0
    }
    for item in prepared:
        output = Path(item["output"])
        if output.resolve() not in generated_paths or not output.exists() or output.stat().st_size == 0:
            raise HTTPException(
                status_code=500,
                detail=f"No se pudo generar el certificado de {item['person']['nombre_completo']}.",
            )

    responsible = next(
        (item for item in team if str(item.get("rol") or "").lower() == "responsable"),
        team[0],
    )
    existing_memo = final_memorandum
    memo_id = str(existing_memo.get("certificate_memorandum_id") or "")
    memo_number = str(existing_memo.get("numero_memorandum") or "")
    if not memo_id or not memo_number:
        raise HTTPException(status_code=500, detail="No se pudo recuperar el memorándum de aprobación final.")

    with get_connection() as conn:
        cur = conn.cursor()
        try:
            for item in prepared:
                person = item["person"]
                if item["current"]:
                    cur.execute(
                        """UPDATE certificado
                        SET horas_certificadas=%s, archivo_url=%s, fecha_emision=%s,
                            codigo_verificacion=%s, estado_id=%s
                        WHERE certificado_id=%s""",
                        (
                            data.horas_certificadas,
                            item["database_path"],
                            emitted,
                            item["verification"],
                            state_emitted,
                            item["certificate_id"],
                        ),
                    )
                else:
                    cur.execute(
                        """INSERT INTO certificado
                        (certificado_id,persona_id,proyecto_id,horas_certificadas,archivo_url,fecha_solicitud,
                         fecha_emision,codigo_verificacion,es_convalidacion,estado_id)
                        VALUES (%s,%s,%s,%s,%s,CURDATE(),%s,%s,0,%s)""",
                        (
                            item["certificate_id"],
                            person["persona_id"],
                            project_id,
                            data.horas_certificadas,
                            item["database_path"],
                            emitted,
                            item["verification"],
                            state_emitted,
                        ),
                    )
            cur.execute(
                """UPDATE rsu_certificate_memorandum
                   SET cantidad_certificados=%s, emitido_por=%s
                   WHERE certificate_memorandum_id=%s""",
                (len(prepared), admin.persona_id, memo_id),
            )
            cur.execute(
                "UPDATE rol_proyecto SET horas_acumuladas=%s WHERE proyecto_id=%s AND activo=1",
                (data.horas_certificadas, project_id),
            )
            cur.execute(
                "UPDATE rsu_project_workflow SET fecha_finalizacion=NOW() WHERE proyecto_id=%s",
                (project_id,),
            )
            _set_workflow(
                cur,
                project_id,
                "finalizado",
                "finalizado",
                admin.persona_id,
                None,
                "Certificados individuales emitidos; el memorándum de aprobación final ya fue remitido tras aprobar el Anexo 7",
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            cur.close()

    await ws_manager.broadcast(
        {
            "type": "ciclo.certificados_emitidos",
            "message": f"Certificados emitidos para {project['titulo']}",
            "data": {"proyecto_id": project_id, "cantidad": len(prepared)},
        }
    )

    # Entrega individual: cada participante con cuenta recibe únicamente el
    # identificador y acceso a su propio certificado.
    for item in prepared:
        person = item["person"]
        account = fetch_one(
            "SELECT usuario_id FROM usuario WHERE persona_id=%s AND activo=1",
            (person["persona_id"],),
        )
        if not account:
            continue
        await notify_users(
            [str(account["usuario_id"])],
            notification_type="certificado_disponible",
            title="Certificado disponible",
            message=f"Tu certificado del proyecto «{project['titulo']}» ya está disponible para descarga.",
            module="Módulo de Certificados",
            priority="informativa",
            entity_type="certificado",
            entity_id=item["certificate_id"],
            project_id=project_id,
            data={
                "proyecto_id": project_id,
                "titulo_proyecto": project["titulo"],
                "certificado_id": item["certificate_id"],
                "pantalla": "proyectos",
            },
            event_key=f"certificado:{item['certificate_id']}:emitido:{emitted.isoformat()}",
            generated_by=admin.nombre_completo,
            important=True,
        )

    if vice.get("usuario_id"):
        await notify_users(
            [str(vice["usuario_id"])],
            notification_type="certificados_emitidos_vicepresidencia",
            title="Certificados emitidos",
            message=f"Se emitieron los certificados del proyecto «{project['titulo']}». El memorándum final relacionado es {memo_number}.",
            module="Módulo de Certificados",
            priority="alta",
            entity_type="memorandum_final",
            entity_id=memo_id,
            project_id=project_id,
            data={"proyecto_id": project_id, "memorandum_certificacion_id": memo_id, "pantalla": "proyectos"},
            event_key=f"proyecto:{project_id}:certificados-emitidos:{emitted.isoformat()}",
            generated_by=admin.nombre_completo,
            important=True,
        )
    return _serialize(project_id, admin)


@router.get("/proyectos/{project_id}/anexos/{annex_number}/pdf")
def download_annex_pdf(
    project_id: str,
    annex_number: int,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    if annex_number not in {1, 2, 3, 4, 5, 6, 7}:
        raise HTTPException(status_code=404, detail="El anexo solicitado no tiene un formato PDF disponible.")
    project = _serialize(project_id, user)
    if annex_number in {4, 7}:
        # Fichas individualizadas de evaluación: no exponer un PDF como emitido
        # antes de la primera revisión. Solo el revisor asignado puede consultar
        # el borrador de su ficha previa a la evaluación.
        issued = any(int(e.get("anexo_numero") or 0) == annex_number for e in project.get("evaluaciones", []))
        reviewer = project.get("permisos", {})
        can_preview = bool(reviewer.get("puede_revisar")) and int(reviewer.get("anexo_revision") or 0) == annex_number
        if not issued and not can_preview:
            raise HTTPException(status_code=404, detail=f"El Anexo {annex_number} aún no fue evaluado ni está habilitado para descarga.")
        project["_autoridades_firma"] = _validate_annex4_authorities(project_id)
    project["_archivos_pdf"] = fetch_all(
        """
        SELECT workflow_file_id, anexo_numero, proposito, etiqueta, persona_firmante_id,
               archivo_url, nombre_archivo, mime_type, fecha_subida
        FROM rsu_workflow_file
        WHERE proyecto_id=%s
        ORDER BY fecha_subida DESC
        """,
        (project_id,),
    )
    # Los formatos 4 y 7 pueden visualizarse incluso antes de ser emitidos,
    # porque funcionan como pautas oficiales durante la evaluación.
    if annex_number not in {4, 7} and str(annex_number) not in (project.get("anexos") or {}):
        raise HTTPException(status_code=404, detail=f"El Anexo {annex_number} todavía no fue presentado.")

    fingerprint = annex_pdf_fingerprint(project, annex_number)
    output = (
        get_settings().upload_dir.resolve().parent
        / "private_rsu_workflow"
        / project_id
        / "pdf_generados"
        / f"anexo_{annex_number}_{fingerprint[:20]}.pdf"
    )
    etag = f'"{fingerprint}"'
    requested_etags = {
        value.strip().removeprefix("W/").strip()
        for value in str(request.headers.get("if-none-match") or "").split(",")
        if value.strip()
    }
    response_headers = {
        "ETag": etag,
        "Cache-Control": "private, no-cache",
        "X-RSU-Annex-Version": fingerprint,
    }
    if etag in requested_etags or "*" in requested_etags:
        return Response(status_code=304, headers=response_headers)

    try:
        output, fingerprint, regenerated = ensure_annex_pdf_cached(
            output,
            project,
            annex_number,
            ANNEX4_CRITERIA,
            ANNEX7_CRITERIA,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo generar el PDF del Anexo {annex_number}: {exc}") from exc

    response_headers.update(
        {
            "ETag": f'"{fingerprint}"',
            "X-RSU-Annex-Version": fingerprint,
            "X-RSU-Annex-Cache": "regenerated" if regenerated else "hit",
        }
    )
    return FileResponse(
        output,
        filename=f"{_safe_filename_component(project.get('titulo'))}-anexo-{annex_number}.pdf",
        media_type="application/pdf",
        headers=response_headers,
    )


@router.get("/proyectos/{project_id}/expediente-completo")
def download_complete_expedient(project_id: str, user: AuthUser = Depends(get_current_user)):
    project = _serialize(project_id, user)
    project["_archivos_pdf"] = fetch_all(
        """SELECT workflow_file_id,anexo_numero,proposito,etiqueta,persona_firmante_id,
                  archivo_url,nombre_archivo,mime_type,fecha_subida
           FROM rsu_workflow_file WHERE proyecto_id=%s ORDER BY fecha_subida DESC""",
        (project_id,),
    )
    project["_autoridades_firma"] = _annex4_authorities(project_id)
    base = get_settings().upload_dir.resolve().parent / "private_rsu_workflow" / project_id
    pdf_dir = base / "pdf_generados"
    bundle_dir = base / "expediente"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    safe_title = _safe_filename_component(project.get("titulo"))
    zip_path = bundle_dir / f"{safe_title}-documentacion.zip"
    available = sorted(int(value) for value in (project.get("anexos") or {}).keys())
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for annex_number in available:
            output = pdf_dir / f"anexo_{annex_number}.pdf"
            try:
                output, _fingerprint, _regenerated = ensure_annex_pdf_cached(
                    output, project, annex_number, ANNEX4_CRITERIA, ANNEX7_CRITERIA
                )
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"No se pudo generar el Anexo {annex_number} para el expediente: {exc}") from exc
            archive.write(output, arcname=f"{safe_title}-anexo-{annex_number}.pdf")
        memorandum = project.get("memorandum") or {}
        if memorandum:
            memo_pdf = Path(str(memorandum.get("archivo_pdf_url") or ""))
            if not memo_pdf.exists():
                raise HTTPException(
                    status_code=500,
                    detail="El memorándum está registrado, pero su PDF no está disponible. Configura LibreOffice y vuelve a emitir o regenerar el documento.",
                )
            archive.write(memo_pdf, arcname=f"{safe_title}-memorandum_aprobacion.pdf")
        certificate_memorandum = project.get("memorandum_certificacion") or {}
        if certificate_memorandum:
            certificate_memo_pdf = _resolve_database_path(certificate_memorandum.get("archivo_pdf_url"))
            if certificate_memo_pdf.exists():
                archive.write(
                    certificate_memo_pdf,
                    arcname=f"{safe_title}-memorandum_aprobacion_final.pdf",
                )
        certificates = fetch_all(
            """SELECT c.certificado_id,c.archivo_url,c.codigo_verificacion,
                      CONCAT(p.nombres,' ',p.apellidos) AS nombre_completo
               FROM certificado c INNER JOIN persona p ON p.persona_id=c.persona_id
               WHERE c.proyecto_id=%s ORDER BY p.apellidos,p.nombres""",
            (project_id,),
        )
        for certificate in certificates:
            certificate_path = _resolve_database_path(certificate.get("archivo_url"))
            if certificate_path.exists():
                certificate_name = certificate_download_filename(
                    certificate.get("nombre_completo"), project.get("titulo")
                )
                archive.write(
                    certificate_path,
                    arcname=f"Certificados/{certificate_name}",
                )
        manifest = (
            f"PROYECTO: {project.get('titulo') or ''}\n"
            f"TIPO: {project.get('tipo_proyecto') or ''}\n"
            f"PERIODO: {project.get('periodo_academico') or ''}\n"
            f"ANEXOS: {', '.join(str(x) for x in available)}\n"
            f"MEMORÁNDUM DE APROBACIÓN: {(memorandum or {}).get('numero_memorandum') or 'No emitido'}\n"
            f"MEMORÁNDUM DE APROBACIÓN FINAL: {(certificate_memorandum or {}).get('numero_memorandum') or 'No emitido'}\n"
            f"CERTIFICADOS INDIVIDUALES: {len(certificates)}\n"
        )
        archive.writestr("INDICE_EXPEDIENTE.txt", manifest.encode("utf-8"))
    return FileResponse(zip_path, filename=f"{safe_title}-documentacion.zip", media_type="application/zip")


@router.get("/archivos/{file_id}/descargar")
def download_workflow_file(file_id: str, user: AuthUser = Depends(get_current_user)):
    row = fetch_one("SELECT * FROM rsu_workflow_file WHERE workflow_file_id=%s", (file_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    _assert_access(str(row["proyecto_id"]), user)
    path = _resolve_database_path(row["archivo_url"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="El archivo físico no existe.")
    return FileResponse(path, filename=row["nombre_archivo"], media_type=row.get("mime_type") or "application/octet-stream")


@router.get("/certificados/{certificate_id}/descargar")
def download_certificate(certificate_id: str, user: AuthUser = Depends(get_current_user)):
    row = fetch_one(
        """SELECT c.*, CONCAT_WS(' ', pe.nombres, pe.apellidos) AS nombre_completo, pr.titulo
           FROM certificado c
           INNER JOIN persona pe ON pe.persona_id=c.persona_id
           INNER JOIN proyecto pr ON pr.proyecto_id=c.proyecto_id
           WHERE c.certificado_id=%s LIMIT 1""",
        (certificate_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Certificado no encontrado.")
    _assert_access(str(row["proyecto_id"]), user)
    if not _is_admin(user) and str(row["persona_id"]) != user.persona_id:
        raise HTTPException(status_code=403, detail="Solo puedes descargar tu certificado.")
    path = _resolve_database_path(row["archivo_url"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="El archivo físico no existe.")
    filename = certificate_download_filename(row.get("nombre_completo"), row.get("titulo"))
    return FileResponse(path, filename=filename, media_type="application/pdf")
