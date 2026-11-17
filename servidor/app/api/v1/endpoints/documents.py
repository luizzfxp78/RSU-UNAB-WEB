from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import get_current_user, require_admin
from app.core.config import get_settings
from app.db.mysql import execute, fetch_all, fetch_one
from app.schemas.auth import AuthUser
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/documentos", tags=["Documentos y anexos"])


@router.get("/por-proyecto/{proyecto_id}")
def listar_documentos_proyecto(proyecto_id: str, _: AuthUser = Depends(require_admin)):
    return fetch_all(
        """
        SELECT
            d.*, td.nombre AS tipo_documento, td.anexo_referencia, td.etapa,
            ce.nombre AS estado, ep.nombre AS etapa_proyecto
        FROM documento d
        INNER JOIN etapa_proyecto ep ON ep.etapa_proyecto_id = d.etapa_proyecto_id
        INNER JOIN tipo_documento td ON td.tipo_documento_id = d.tipo_documento_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id = d.estado_id
        WHERE ep.proyecto_id = %s
        ORDER BY d.fecha_subida DESC
        """,
        (proyecto_id,),
    )


@router.post("/subir")
async def subir_documento(
    etapa_proyecto_id: str = Form(...),
    tipo_documento_id: str = Form(...),
    estado_id: str = Form(...),
    observaciones: str | None = Form(None),
    file: UploadFile = File(...),
    current_user: AuthUser = Depends(require_admin),
):
    etapa = fetch_one("SELECT proyecto_id FROM etapa_proyecto WHERE etapa_proyecto_id = %s", (etapa_proyecto_id,))
    if etapa is None:
        raise HTTPException(status_code=404, detail="Etapa de proyecto no encontrada.")

    safe_name = Path(file.filename or "documento.bin").name
    extension = Path(safe_name).suffix
    documento_id = str(uuid4())
    project_dir = get_settings().upload_dir / "documents" / etapa["proyecto_id"]
    project_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{documento_id}{extension}"
    stored_path = project_dir / stored_name

    content = await file.read()
    stored_path.write_bytes(content)

    execute(
        """
        INSERT INTO documento
        (documento_id, etapa_proyecto_id, tipo_documento_id, subido_por, archivo_url,
         nombre_archivo, estado_id, observaciones)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            documento_id,
            etapa_proyecto_id,
            tipo_documento_id,
            current_user.persona_id,
            str(stored_path.as_posix()),
            safe_name,
            estado_id,
            observaciones,
        ),
    )
    await ws_manager.broadcast(
        {
            "type": "document.uploaded",
            "message": "Documento subido al proyecto.",
            "data": {"documento_id": documento_id, "proyecto_id": etapa["proyecto_id"], "nombre_archivo": safe_name},
        }
    )
    return fetch_one("SELECT * FROM documento WHERE documento_id = %s", (documento_id,))


@router.get("/{documento_id}/descargar")
def descargar_documento(documento_id: str, _: AuthUser = Depends(require_admin)):
    row = fetch_one("SELECT archivo_url, nombre_archivo FROM documento WHERE documento_id = %s", (documento_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    path = Path(row["archivo_url"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="El archivo físico no existe en el servidor.")
    return FileResponse(path, filename=row.get("nombre_archivo") or path.name)


@router.patch("/{documento_id}/estado")
async def cambiar_estado_documento(
    documento_id: str,
    estado_id: str = Form(...),
    observaciones: str | None = Form(None),
    _: AuthUser = Depends(require_admin),
):
    row = fetch_one(
        """
        SELECT d.documento_id, ep.proyecto_id
        FROM documento d
        INNER JOIN etapa_proyecto ep ON ep.etapa_proyecto_id = d.etapa_proyecto_id
        WHERE d.documento_id = %s
        """,
        (documento_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    execute(
        "UPDATE documento SET estado_id=%s, observaciones=%s WHERE documento_id=%s",
        (estado_id, observaciones, documento_id),
    )
    await ws_manager.broadcast({"type": "document.state_changed", "message": "Estado de documento actualizado.", "data": {"documento_id": documento_id, "proyecto_id": row["proyecto_id"]}})
    return fetch_one("SELECT * FROM documento WHERE documento_id = %s", (documento_id,))
