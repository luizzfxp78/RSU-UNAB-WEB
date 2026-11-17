import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.api.v1.router import api_router
from app.api.v1.endpoints.websockets import router as websocket_router
from app.core.config import get_settings
from app.core.security import decode_token
from app.db.workflow_schema import ensure_workflow_schema
from app.db.project_lifecycle_schema import ensure_project_lifecycle_schema
from app.db.notification_schema import ensure_notification_schema
from app.db.specific_activity_schema import ensure_specific_activity_schema
from app.db.volunteer_schema import ensure_volunteer_schema
from app.db.governance_schema import ensure_governance_schema
from app.services.notification_jobs import notification_reminder_loop
from app.services.audit_service import record_audit_event
from app.services.password_security_audit import audit_stored_password_hashes

settings = get_settings()
_notification_task: asyncio.Task | None = None

app = FastAPI(
    title=settings.app_name,
    version="93.0.0",
    description="Servidor REST + WebSocket y plataforma web institucional RSU UNAB.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(websocket_router)


@app.middleware("http")
async def audit_write_operations(request: Request, call_next):
    """Registra altas, ediciones y eliminaciones realizadas por usuarios autenticados."""
    response = await call_next(request)
    method = request.method.upper()
    path = request.url.path
    should_audit = (
        method in {"POST", "PUT", "PATCH", "DELETE"}
        and path.startswith(settings.api_prefix)
        and path not in {f"{settings.api_prefix}/auth/login"}
        and 200 <= int(response.status_code) < 400
    )
    if should_audit:
        token = ""
        authorization = str(request.headers.get("authorization") or "")
        if authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
        usuario_id = None
        if token:
            try:
                usuario_id = str(decode_token(token).get("sub") or "") or None
            except Exception:
                usuario_id = None
        route = request.scope.get("route")
        route_name = getattr(route, "name", None)
        try:
            await run_in_threadpool(
                record_audit_event,
                usuario_id=usuario_id,
                method=method,
                path=path,
                status_code=int(response.status_code),
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                detail=str(route_name or "Operación completada"),
            )
        except Exception:
            # La auditoría nunca debe invalidar una operación ya confirmada.
            pass
    return response

# Permite servir archivos centralizados si más adelante quieres mostrarlos desde la red local.
# Los archivos y firmas no se exponen mediante rutas estáticas públicas.
# Los documentos se descargan exclusivamente por endpoints autenticados.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/app", StaticFiles(directory=str(WEB_DIR), html=True), name="rsu_web")


@app.on_event("startup")
async def initialize_workflow_schema() -> None:
    # Extiende de manera compatible la base original con el detalle editable de anexos y evaluaciones.
    global _notification_task
    ensure_workflow_schema()
    ensure_project_lifecycle_schema()
    ensure_notification_schema()
    ensure_specific_activity_schema()
    ensure_volunteer_schema()
    ensure_governance_schema()
    audit_stored_password_hashes()
    _notification_task = asyncio.create_task(notification_reminder_loop())


@app.on_event("shutdown")
async def stop_notification_jobs() -> None:
    global _notification_task
    if _notification_task is not None:
        _notification_task.cancel()
        try:
            await _notification_task
        except asyncio.CancelledError:
            pass
        _notification_task = None


@app.get("/")
def root():
    return RedirectResponse(url="/app/", status_code=307)
