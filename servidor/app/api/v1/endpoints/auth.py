from fastapi import APIRouter, HTTPException, status

from app.core.security import create_access_token, verify_password
from app.db.mysql import execute, fetch_one
from app.schemas.auth import AuthUser, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["Autenticación"])


def _find_user(username: str) -> dict | None:
    return fetch_one(
        """
        SELECT
            u.usuario_id,
            u.persona_id,
            u.username,
            u.password_hash,
            u.activo,
            p.activo AS persona_activa,
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
                INNER JOIN rsu_system_role sr
                    ON sr.system_role_id = usr.system_role_id
                WHERE usr.usuario_id = u.usuario_id
                  AND usr.activo = 1
                  AND sr.activo = 1
                ORDER BY CASE sr.codigo
                    WHEN 'director_rsu' THEN 1
                    WHEN 'vicepresidente_academico' THEN 2
                    WHEN 'director_facultad' THEN 3
                    WHEN 'administrador_rsu' THEN 4
                    WHEN 'administrativo_normal' THEN 5
                    ELSE 9
                END
                LIMIT 1
            ) AS rol_sistema,
            (
                SELECT usr.facultad
                FROM rsu_user_system_role usr
                INNER JOIN rsu_system_role sr
                    ON sr.system_role_id = usr.system_role_id
                WHERE usr.usuario_id = u.usuario_id
                  AND usr.activo = 1
                  AND sr.activo = 1
                  AND NULLIF(TRIM(usr.facultad), '') IS NOT NULL
                ORDER BY usr.fecha_asignacion DESC
                LIMIT 1
            ) AS facultad_asignada
        FROM usuario u
        INNER JOIN persona p ON p.persona_id = u.persona_id
        LEFT JOIN contacto c ON c.persona_id = p.persona_id
        LEFT JOIN docente d ON d.persona_id = p.persona_id
        LEFT JOIN estudiante e ON e.persona_id = p.persona_id
        LEFT JOIN administrativo a ON a.persona_id = p.persona_id
        WHERE u.username = %s
        LIMIT 1;
        """,
        (username.strip(),),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    row = _find_user(payload.username)
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="El usuario no existe.")

    if not bool(row["activo"]) or not bool(row.get("persona_activa", 1)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El usuario o la persona está inactiva.")

    if not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="La contraseña es incorrecta.")

    execute("UPDATE usuario SET ultimo_acceso = NOW() WHERE usuario_id = %s", (row["usuario_id"],))

    user = AuthUser(
        usuario_id=row["usuario_id"],
        persona_id=row["persona_id"],
        username=row["username"],
        nombre_completo=row["nombre_completo"],
        dni=row["dni"],
        tipo_usuario=row["tipo_usuario"],
        email_institucional=row.get("email_institucional"),
        detalle=row.get("detalle"),
        rol_sistema=row.get("rol_sistema"),
        facultad_asignada=row.get("facultad_asignada"),
        domicilio=row.get("domicilio"),
        condicion=row.get("condicion"),
    )
    token = create_access_token(
        subject=user.usuario_id,
        extra_claims={
            "persona_id": user.persona_id,
            "tipo_usuario": user.tipo_usuario,
            "rol_sistema": user.rol_sistema,
            "facultad_asignada": user.facultad_asignada,
        },
    )
    return TokenResponse(access_token=token, user=user)
