from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_token
from app.db.mysql import fetch_one
from app.schemas.auth import AuthUser

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> AuthUser:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado.")

    payload = decode_token(credentials.credentials)
    usuario_id = payload.get("sub")
    if not usuario_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token sin usuario.")

    row = fetch_one(
        """
        SELECT
            u.usuario_id,
            u.persona_id,
            u.username,
            CONCAT(p.nombres, ' ', p.apellidos) AS nombre_completo,
            p.dni,
            c.email_institucional,
            c.direccion AS domicilio,
            CASE
                WHEN d.persona_id IS NOT NULL THEN NULLIF(TRIM(d.condicion), '')
                WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                WHEN a.persona_id IS NOT NULL THEN COALESCE(NULLIF(TRIM(a.cargo), ''), 'Administrativo')
                ELSE 'Usuario'
            END AS condicion,
            CASE
                WHEN d.persona_id IS NOT NULL THEN 'Docente'
                WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                WHEN a.persona_id IS NOT NULL THEN 'Administrativo'
                ELSE 'Usuario'
            END AS tipo_usuario,
            CASE
                WHEN d.persona_id IS NOT NULL THEN COALESCE(d.categoria, 'Docente')
                WHEN e.persona_id IS NOT NULL THEN COALESCE(e.codigo_estudiante, 'Estudiante')
                WHEN a.persona_id IS NOT NULL THEN COALESCE(a.cargo, 'Administrativo')
                ELSE NULL
            END AS detalle,
            (
                SELECT sr.codigo
                FROM rsu_user_system_role usr
                INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id
                WHERE usr.usuario_id=u.usuario_id AND usr.activo=1 AND sr.activo=1
                ORDER BY CASE sr.codigo
                    WHEN 'director_rsu' THEN 1
                    WHEN 'vicepresidente_academico' THEN 2
                    WHEN 'director_facultad' THEN 3
                    WHEN 'administrador_rsu' THEN 4
                    WHEN 'administrativo_normal' THEN 5
                    ELSE 9 END
                LIMIT 1
            ) AS rol_sistema,
            (
                SELECT usr.facultad
                FROM rsu_user_system_role usr
                INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id
                WHERE usr.usuario_id=u.usuario_id AND usr.activo=1 AND sr.activo=1
                  AND NULLIF(TRIM(usr.facultad),'') IS NOT NULL
                ORDER BY usr.fecha_asignacion DESC LIMIT 1
            ) AS facultad_asignada
        FROM usuario u
        INNER JOIN persona p ON p.persona_id = u.persona_id
        LEFT JOIN contacto c ON c.persona_id = p.persona_id
        LEFT JOIN docente d ON d.persona_id = p.persona_id
        LEFT JOIN estudiante e ON e.persona_id = p.persona_id
        LEFT JOIN administrativo a ON a.persona_id = p.persona_id
        WHERE u.usuario_id = %s AND u.activo = 1 AND p.activo = 1
        LIMIT 1;
        """,
        (usuario_id,),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no válido o inactivo.")
    return AuthUser(**row)



def deny_certificate_for_simulation(project_id: str) -> None:
    """Bloquea certificación oficial de expedientes adelantados por la herramienta de ensayo."""
    simulated = fetch_one(
        """SELECT audit_id FROM rsu_audit_log
           WHERE recurso_id=%s AND accion='SIMULACIÓN' LIMIT 1""",
        (project_id,),
    )
    if simulated:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Expediente marcado como simulación: no es posible emitir certificados "
                    "oficiales tras adelantar etapas en pruebas. Crea un expediente nuevo "
                    "y completa las evaluaciones reglamentarias."),
        )


def require_admin(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
    role = str(current_user.rol_sistema or "").lower()
    if role:
        allowed = role in {"administrador_rsu", "director_rsu"}
    else:
        # Compatibilidad con instalaciones anteriores a los roles funcionales.
        allowed = current_user.tipo_usuario.lower() == "administrativo"
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo administradores de RSU.")
    return current_user


def require_project_reviewer(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
    role = str(current_user.rol_sistema or "").lower()
    if role not in {"administrador_rsu", "director_rsu", "director_facultad"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el administrador de RSU o el Director de Facultad asignado puede revisar el proyecto.")
    return current_user


def require_vicepresident(current_user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if str(current_user.rol_sistema or "").lower() != "vicepresidente_academico":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo el vicepresidente académico puede realizar esta acción.")
    return current_user
