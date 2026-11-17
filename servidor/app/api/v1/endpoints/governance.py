from __future__ import annotations

import hashlib
import os
import re
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, require_admin
from app.core.config import get_settings
from app.db.mysql import execute, fetch_all, fetch_one, get_connection
from app.schemas.auth import AuthUser

router = APIRouter(tags=["Auditoría y plantillas"])

MAX_TEMPLATE_BYTES = 10 * 1024 * 1024
ALLOWED_MODULES = {
    "general": "General",
    "proyectos_rsu": "Proyectos RSU",
    "actividades_especificas": "Actividades específicas",
    "voluntariado": "Voluntariado RSU",
}


class AnnexTemplateMetadata(BaseModel):
    modulo: str = Field(min_length=1, max_length=60)
    codigo_anexo: str = Field(min_length=1, max_length=40)
    nombre: str = Field(min_length=2, max_length=180)
    descripcion: str | None = Field(default=None, max_length=500)


def _is_admin(user: AuthUser) -> bool:
    role = str(user.rol_sistema or "").strip().lower()
    return role in {"administrador_rsu", "director_rsu"} or (
        not role and str(user.tipo_usuario or "").strip().lower() == "administrativo"
    )


def _normalize_module(value: str) -> str:
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "proyectos": "proyectos_rsu",
        "proyecto_rsu": "proyectos_rsu",
        "actividad_especifica": "actividades_especificas",
        "actividades": "actividades_especificas",
        "voluntariado_rsu": "voluntariado",
    }
    key = aliases.get(key, key)
    if key not in ALLOWED_MODULES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Selecciona un módulo válido para la plantilla.",
        )
    return key


def _normalize_annex_code(value: str) -> str:
    raw = re.sub(r"\s+", " ", str(value or "").strip())
    if not raw:
        raise HTTPException(status_code=422, detail="Indica el código o número del anexo.")
    if len(raw) > 40:
        raise HTTPException(status_code=422, detail="El código del anexo es demasiado largo.")
    return raw


def _template_storage_dir() -> Path:
    target = get_settings().private_storage_dir / "plantillas_anexos"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _template_row(row: dict, can_manage: bool = False) -> dict:
    result = dict(row)
    result["tamano_mb"] = round(int(result.get("tamano_bytes") or 0) / (1024 * 1024), 2)
    result["modulo_nombre"] = ALLOWED_MODULES.get(str(result.get("modulo") or ""), str(result.get("modulo") or ""))
    result["can_manage"] = bool(can_manage)
    result.pop("ruta_archivo", None)
    return result


@router.get("/auditoria")
def list_audit_log(
    accion: str | None = Query(default=None, max_length=24),
    modulo: str | None = Query(default=None, max_length=90),
    usuario: str | None = Query(default=None, max_length=180),
    desde: date | None = None,
    hasta: date | None = None,
    q: str | None = Query(default=None, max_length=180),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: AuthUser = Depends(require_admin),
) -> dict:
    where = ["1=1"]
    params: list[object] = []
    if accion and accion.strip().lower() not in {"todos", "all"}:
        where.append("a.accion=%s")
        params.append(accion.strip().upper())
    if modulo and modulo.strip().lower() not in {"todos", "all"}:
        where.append("a.modulo=%s")
        params.append(modulo.strip())
    if usuario and usuario.strip().lower() not in {"todos", "all"}:
        where.append("a.usuario_responsable=%s")
        params.append(usuario.strip())
    if desde:
        where.append("DATE(a.fecha_hora)>=%s")
        params.append(desde.isoformat())
    if hasta:
        where.append("DATE(a.fecha_hora)<=%s")
        params.append(hasta.isoformat())
    if q and q.strip():
        term = f"%{q.strip()}%"
        where.append(
            "(a.usuario_responsable LIKE %s OR a.username LIKE %s OR a.modulo LIKE %s "
            "OR a.entidad LIKE %s OR a.ruta LIKE %s OR a.detalle LIKE %s)"
        )
        params.extend([term] * 6)
    where_sql = " AND ".join(where)

    total_row = fetch_one(f"SELECT COUNT(*) AS total FROM rsu_audit_log a WHERE {where_sql}", params) or {}
    items = fetch_all(
        f"""
        SELECT a.audit_id, a.accion, a.modulo, a.entidad, a.recurso_id,
               a.metodo_http, a.ruta, a.detalle, a.estado_http,
               a.usuario_responsable, a.username, a.rol_sistema,
               a.direccion_ip, a.fecha_hora
        FROM rsu_audit_log a
        WHERE {where_sql}
        ORDER BY a.fecha_hora DESC
        LIMIT %s OFFSET %s
        """,
        [*params, int(limit), int(offset)],
    )
    summary_rows = fetch_all(
        f"""
        SELECT accion, COUNT(*) AS cantidad
        FROM rsu_audit_log a
        WHERE {where_sql}
        GROUP BY accion
        """,
        params,
    )
    modules = fetch_all(
        "SELECT DISTINCT modulo FROM rsu_audit_log WHERE NULLIF(TRIM(modulo),'') IS NOT NULL ORDER BY modulo"
    )
    users = fetch_all(
        """
        SELECT DISTINCT usuario_responsable
        FROM rsu_audit_log
        WHERE NULLIF(TRIM(usuario_responsable),'') IS NOT NULL
        ORDER BY usuario_responsable
        """
    )
    summary = {"REGISTRO": 0, "EDICIÓN": 0, "ELIMINACIÓN": 0}
    for row in summary_rows:
        summary[str(row.get("accion") or "").upper()] = int(row.get("cantidad") or 0)
    summary["TOTAL"] = int(total_row.get("total") or 0)
    return {
        "items": items,
        "total": int(total_row.get("total") or 0),
        "limit": int(limit),
        "offset": int(offset),
        "summary": summary,
        "modules": [str(row.get("modulo") or "") for row in modules],
        "users": [str(row.get("usuario_responsable") or "") for row in users],
    }


@router.get("/plantillas-anexos")
def list_annex_templates(
    modulo: str | None = Query(default=None, max_length=60),
    q: str | None = Query(default=None, max_length=180),
    current_user: AuthUser = Depends(get_current_user),
) -> dict:
    where = ["t.activo=1", "t.es_actual=1"]
    params: list[object] = []
    if modulo and modulo.strip().lower() not in {"todos", "all"}:
        where.append("t.modulo=%s")
        params.append(_normalize_module(modulo))
    if q and q.strip():
        term = f"%{q.strip()}%"
        where.append("(t.codigo_anexo LIKE %s OR t.nombre LIKE %s OR t.descripcion LIKE %s)")
        params.extend([term, term, term])
    rows = fetch_all(
        f"""
        SELECT t.template_id, t.modulo, t.codigo_anexo, t.nombre, t.descripcion,
               t.version, t.nombre_archivo_original, t.tamano_bytes, t.sha256,
               t.usuario_carga_nombre, t.fecha_creacion, t.fecha_actualizacion
        FROM rsu_annex_reference_template t
        WHERE {' AND '.join(where)}
        ORDER BY FIELD(t.modulo,'proyectos_rsu','actividades_especificas','voluntariado','general'),
                 t.codigo_anexo, t.version DESC
        """,
        params,
    )
    return {
        "items": [_template_row(row, _is_admin(current_user)) for row in rows],
        "modules": [{"codigo": code, "nombre": name} for code, name in ALLOWED_MODULES.items()],
        "max_size_bytes": MAX_TEMPLATE_BYTES,
        "can_manage": _is_admin(current_user),
    }


@router.post("/plantillas-anexos", status_code=status.HTTP_201_CREATED)
async def upload_annex_template(
    modulo: str = Form(...),
    codigo_anexo: str = Form(...),
    nombre: str = Form(...),
    descripcion: str = Form(default=""),
    archivo: UploadFile = File(...),
    current_user: AuthUser = Depends(require_admin),
) -> dict:
    module_key = _normalize_module(modulo)
    annex_code = _normalize_annex_code(codigo_anexo)
    display_name = str(nombre or "").strip()
    if len(display_name) < 2 or len(display_name) > 180:
        raise HTTPException(status_code=422, detail="El nombre debe tener entre 2 y 180 caracteres.")
    original_name = Path(str(archivo.filename or "plantilla.pdf")).name
    if Path(original_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=422, detail="Solo se permiten plantillas en formato PDF.")

    content = bytearray()
    while True:
        chunk = await archivo.read(1024 * 1024)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) >= MAX_TEMPLATE_BYTES:
            raise HTTPException(status_code=413, detail="La plantilla PDF debe pesar menos de 10 MB.")
    await archivo.close()
    if not content or not bytes(content[:5]).startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail="El archivo no contiene un PDF válido.")

    storage = _template_storage_dir()
    stored_name = f"{uuid4().hex}.pdf"
    final_path = storage / stored_name
    temporary_path = storage / f".{stored_name}.part"
    temporary_path.write_bytes(bytes(content))
    digest = hashlib.sha256(content).hexdigest()

    template_id = str(uuid4())
    try:
        with get_connection() as conn:
            cursor = conn.cursor(dictionary=True, buffered=True)
            cursor.execute(
                """
                SELECT COALESCE(MAX(version),0) AS ultima_version
                FROM rsu_annex_reference_template
                WHERE modulo=%s AND codigo_anexo=%s
                FOR UPDATE
                """,
                (module_key, annex_code),
            )
            version_row = cursor.fetchone() or {}
            version = int(version_row.get("ultima_version") or 0) + 1
            cursor.execute(
                """
                UPDATE rsu_annex_reference_template
                SET es_actual=0
                WHERE modulo=%s AND codigo_anexo=%s AND es_actual=1
                """,
                (module_key, annex_code),
            )
            os.replace(temporary_path, final_path)
            cursor.execute(
                """
                INSERT INTO rsu_annex_reference_template (
                  template_id, modulo, codigo_anexo, nombre, descripcion, version,
                  nombre_archivo_original, ruta_archivo, tamano_bytes, sha256,
                  mime_type, usuario_carga_id, usuario_carga_nombre,
                  es_actual, activo, fecha_creacion, fecha_actualizacion
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'application/pdf',%s,%s,1,1,NOW(),NOW())
                """,
                (
                    template_id, module_key, annex_code, display_name,
                    str(descripcion or "").strip()[:500] or None, version,
                    original_name, stored_name, len(content), digest,
                    current_user.usuario_id, current_user.nombre_completo,
                ),
            )
            conn.commit()
            cursor.close()
    except Exception:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise

    row = fetch_one(
        """
        SELECT template_id, modulo, codigo_anexo, nombre, descripcion, version,
               nombre_archivo_original, tamano_bytes, sha256,
               usuario_carga_nombre, fecha_creacion, fecha_actualizacion
        FROM rsu_annex_reference_template WHERE template_id=%s
        """,
        (template_id,),
    ) or {}
    return _template_row(row, True)


@router.put("/plantillas-anexos/{template_id}")
def update_annex_template_metadata(
    template_id: str,
    payload: AnnexTemplateMetadata,
    _: AuthUser = Depends(require_admin),
) -> dict:
    current = fetch_one(
        "SELECT template_id FROM rsu_annex_reference_template WHERE template_id=%s AND activo=1 LIMIT 1",
        (template_id,),
    )
    if not current:
        raise HTTPException(status_code=404, detail="La plantilla no existe o fue eliminada.")
    module_key = _normalize_module(payload.modulo)
    annex_code = _normalize_annex_code(payload.codigo_anexo)
    execute(
        """
        UPDATE rsu_annex_reference_template
        SET modulo=%s, codigo_anexo=%s, nombre=%s, descripcion=%s, fecha_actualizacion=NOW()
        WHERE template_id=%s AND activo=1
        """,
        (
            module_key, annex_code, payload.nombre.strip(),
            str(payload.descripcion or "").strip()[:500] or None, template_id,
        ),
    )
    row = fetch_one(
        """
        SELECT template_id, modulo, codigo_anexo, nombre, descripcion, version,
               nombre_archivo_original, tamano_bytes, sha256,
               usuario_carga_nombre, fecha_creacion, fecha_actualizacion
        FROM rsu_annex_reference_template WHERE template_id=%s
        """,
        (template_id,),
    ) or {}
    return _template_row(row, True)


@router.delete("/plantillas-anexos/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_annex_template(
    template_id: str,
    _: AuthUser = Depends(require_admin),
) -> None:
    row = fetch_one(
        "SELECT ruta_archivo FROM rsu_annex_reference_template WHERE template_id=%s AND activo=1 LIMIT 1",
        (template_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="La plantilla no existe o ya fue eliminada.")
    execute(
        """
        UPDATE rsu_annex_reference_template
        SET activo=0, es_actual=0, fecha_actualizacion=NOW()
        WHERE template_id=%s
        """,
        (template_id,),
    )
    # Se conserva el archivo físico para trazabilidad y recuperación administrativa.
    return None


@router.get("/plantillas-anexos/{template_id}/descargar")
def download_annex_template(
    template_id: str,
    _: AuthUser = Depends(get_current_user),
) -> FileResponse:
    row = fetch_one(
        """
        SELECT nombre_archivo_original, ruta_archivo
        FROM rsu_annex_reference_template
        WHERE template_id=%s AND activo=1 AND es_actual=1
        LIMIT 1
        """,
        (template_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="La plantilla solicitada no está disponible.")
    stored_path = Path(str(row.get("ruta_archivo") or ""))
    path = stored_path if stored_path.is_absolute() else (_template_storage_dir() / stored_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="El archivo PDF de la plantilla no se encuentra en el servidor.")
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=str(row.get("nombre_archivo_original") or "plantilla_anexo.pdf"),
    )
