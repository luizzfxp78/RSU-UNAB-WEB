from __future__ import annotations

import base64
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import require_admin
from app.core.config import get_settings
from app.core.password_policy import PASSWORD_POLICY_MESSAGE, validate_password_policy
from app.core.security import hash_password
from app.db.mysql import fetch_all, fetch_one, get_connection
from app.schemas.auth import AuthUser
from app.schemas.roles_authorities import (
    AuthorityAssignmentIn,
    AuthorityAssignmentStatusIn,
    SystemRoleIn,
)

router = APIRouter(prefix="/roles-autoridades", tags=["Roles y autoridades"])

RESERVED_ROLE_CODES = {
    "administrador_rsu",
    "administrativo_normal",
    "director_rsu",
    "director_facultad",
    "vicepresidente_academico",
}
GLOBAL_SINGLETON_ROLES = {"director_rsu", "vicepresidente_academico"}
FACULTY_SINGLETON_ROLES = {"director_facultad"}
FACULTY_REQUIRED_ROLES = FACULTY_SINGLETON_ROLES
NON_AUTHORITY_ROLES = {"administrativo_normal"}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _role_rows() -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT
            sr.system_role_id,
            sr.codigo,
            sr.nombre,
            sr.descripcion,
            sr.activo,
            COUNT(usr.user_system_role_id) AS total_asignaciones,
            SUM(CASE WHEN usr.activo=1 THEN 1 ELSE 0 END) AS asignaciones_activas
        FROM rsu_system_role sr
        LEFT JOIN rsu_user_system_role usr ON usr.system_role_id=sr.system_role_id
        WHERE sr.codigo <> 'jefe_unidad_facultad'
        GROUP BY sr.system_role_id,sr.codigo,sr.nombre,sr.descripcion,sr.activo
        ORDER BY sr.activo DESC, sr.nombre
        """
    )
    for row in rows:
        row["es_reservado"] = str(row.get("codigo") or "") in RESERVED_ROLE_CODES
    return rows


def _person_rows() -> list[dict[str, Any]]:
    """Devuelve docentes y administrativos activos, tengan o no una cuenta.

    La versión anterior usaba INNER JOIN con ``usuario`` y por eso ocultaba a
    los docentes creados sin cuenta de acceso. El módulo ahora los muestra y
    permite crear o reactivar su cuenta al guardar la asignación institucional.
    """
    return fetch_all(
        """
        SELECT
            p.persona_id,
            COALESCE(u.usuario_id, '') AS usuario_id,
            CONCAT(p.apellidos, ', ', p.nombres) AS nombre_completo,
            p.dni,
            COALESCE(u.username, '') AS username,
            p.activo,
            COALESCE(u.activo, 0) AS usuario_activo,
            CASE WHEN u.usuario_id IS NULL THEN 0 ELSE 1 END AS tiene_cuenta,
            c.email_institucional,
            CASE
                WHEN d.persona_id IS NOT NULL THEN 'Docente'
                WHEN a.persona_id IS NOT NULL THEN 'Administrativo'
                ELSE 'Persona'
            END AS tipo_persona,
            COALESCE(dd.facultad, NULL) AS facultad_origen,
            GROUP_CONCAT(DISTINCT sr.nombre ORDER BY sr.nombre SEPARATOR ', ') AS roles_actuales
        FROM persona p
        LEFT JOIN usuario u ON u.persona_id=p.persona_id
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN departamento_academico dd ON dd.departamento_academico_id=d.departamento_academico_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_user_system_role usr ON usr.usuario_id=u.usuario_id AND usr.activo=1
        LEFT JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id AND sr.activo=1
        WHERE p.activo=1
          AND (d.persona_id IS NOT NULL OR a.persona_id IS NOT NULL)
        GROUP BY p.persona_id,u.usuario_id,p.apellidos,p.nombres,p.dni,u.username,p.activo,u.activo,
                 c.email_institucional,d.persona_id,a.persona_id,dd.facultad
        ORDER BY
            CASE WHEN d.persona_id IS NOT NULL THEN 0 ELSE 1 END,
            p.apellidos,p.nombres
        """
    )


def _assignment_query(where: str = "", params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    rows = fetch_all(
        f"""
        SELECT
            usr.user_system_role_id,
            usr.usuario_id,
            usr.system_role_id,
            usr.facultad,
            usr.activo,
            usr.fecha_asignacion,
            sr.codigo AS rol_codigo,
            sr.nombre AS rol_nombre,
            sr.descripcion AS rol_descripcion,
            p.persona_id,
            CONCAT(p.apellidos, ', ', p.nombres) AS persona_nombre,
            p.dni,
            u.username,
            c.email_institucional,
            CASE
                WHEN d.persona_id IS NOT NULL THEN 'Docente'
                WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                WHEN af.tipo_vinculo='no_docente' THEN 'No docente'
                WHEN af.tipo_vinculo='egresado' THEN 'Egresado'
                WHEN af.tipo_vinculo='graduado' THEN 'Graduado'
                WHEN a.persona_id IS NOT NULL THEN 'Administrativo'
                ELSE 'Persona'
            END AS tipo_persona,
            ap.cargo_oficial,
            ap.oficina,
            ap.resolucion_designacion,
            ap.fecha_inicio,
            ap.fecha_fin,
            ap.firma_url,
            CASE WHEN NULLIF(TRIM(ap.firma_url),'') IS NULL THEN 0 ELSE 1 END AS firma_disponible
        FROM rsu_user_system_role usr
        INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id
        INNER JOIN usuario u ON u.usuario_id=usr.usuario_id
        INNER JOIN persona p ON p.persona_id=u.persona_id
        LEFT JOIN contacto c ON c.persona_id=p.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN estudiante e ON e.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id
        {where}
          {"AND" if where else "WHERE"} sr.codigo <> 'jefe_unidad_facultad'
        ORDER BY usr.activo DESC, sr.nombre, p.apellidos, p.nombres
        """,
        params,
    )
    return rows


def _faculties() -> list[str]:
    rows = fetch_all(
        """
        SELECT DISTINCT TRIM(facultad) AS facultad
        FROM departamento_academico
        WHERE activo=1 AND NULLIF(TRIM(facultad),'') IS NOT NULL
        ORDER BY facultad
        """
    )
    return [str(row["facultad"]) for row in rows if row.get("facultad")]


def _stats(assignments: list[dict[str, Any]], faculties: list[str]) -> dict[str, int]:
    active = [item for item in assignments if bool(item.get("activo"))]
    active_authorities = [item for item in active if item.get("rol_codigo") not in NON_AUTHORITY_ROLES]
    covered = {
        str(item.get("facultad") or "").strip().lower()
        for item in active
        if item.get("rol_codigo") in FACULTY_SINGLETON_ROLES and _clean(item.get("facultad"))
    }
    incomplete = sum(
        1
        for item in active_authorities
        if not _clean(item.get("cargo_oficial"))
        or (item.get("rol_codigo") in FACULTY_REQUIRED_ROLES and not _clean(item.get("facultad")))
    )
    return {
        "roles_activos": sum(1 for role in _role_rows() if bool(role.get("activo"))),
        "autoridades_activas": len(active_authorities),
        "facultades_cubiertas": len(covered),
        "facultades_totales": len(faculties),
        "perfiles_incompletos": incomplete,
    }


@router.get("/resumen")
def resumen(_: AuthUser = Depends(require_admin)):
    assignments = _assignment_query()
    faculties = _faculties()
    return {
        "roles": _role_rows(),
        "personas": _person_rows(),
        "facultades": faculties,
        "asignaciones": assignments,
        "estadisticas": _stats(assignments, faculties),
    }


@router.post("/roles")
def crear_rol(data: SystemRoleIn, _: AuthUser = Depends(require_admin)):
    role_id = str(uuid4())
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO rsu_system_role
                (system_role_id,codigo,nombre,descripcion,activo)
                VALUES (%s,%s,%s,%s,%s)""",
                (role_id, data.codigo, data.nombre.strip(), _clean(data.descripcion), int(data.activo)),
            )
            conn.commit()
            cur.close()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_friendly_error(exc)) from exc
    return fetch_one("SELECT * FROM rsu_system_role WHERE system_role_id=%s", (role_id,))


@router.put("/roles/{role_id}")
def actualizar_rol(role_id: str, data: SystemRoleIn, _: AuthUser = Depends(require_admin)):
    current = fetch_one("SELECT * FROM rsu_system_role WHERE system_role_id=%s", (role_id,))
    if not current:
        raise HTTPException(status_code=404, detail="Rol institucional no encontrado.")
    current_code = str(current.get("codigo") or "")
    if current_code in RESERVED_ROLE_CODES and data.codigo != current_code:
        raise HTTPException(status_code=400, detail="El código de un rol reservado no puede modificarse.")
    if not data.activo:
        used = fetch_one(
            "SELECT 1 AS ok FROM rsu_user_system_role WHERE system_role_id=%s AND activo=1 LIMIT 1",
            (role_id,),
        )
        if used:
            raise HTTPException(status_code=409, detail="No puedes desactivar un rol que tiene asignaciones activas.")
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE rsu_system_role
                SET codigo=%s,nombre=%s,descripcion=%s,activo=%s
                WHERE system_role_id=%s""",
                (data.codigo, data.nombre.strip(), _clean(data.descripcion), int(data.activo), role_id),
            )
            conn.commit()
            cur.close()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_friendly_error(exc)) from exc
    return fetch_one("SELECT * FROM rsu_system_role WHERE system_role_id=%s", (role_id,))


@router.delete("/roles/{role_id}")
def desactivar_rol(role_id: str, _: AuthUser = Depends(require_admin)):
    current = fetch_one("SELECT * FROM rsu_system_role WHERE system_role_id=%s", (role_id,))
    if not current:
        raise HTTPException(status_code=404, detail="Rol institucional no encontrado.")
    if str(current.get("codigo") or "") in RESERVED_ROLE_CODES:
        raise HTTPException(status_code=409, detail="Los roles institucionales reservados no pueden eliminarse.")
    used = fetch_one(
        "SELECT 1 AS ok FROM rsu_user_system_role WHERE system_role_id=%s AND activo=1 LIMIT 1",
        (role_id,),
    )
    if used:
        raise HTTPException(status_code=409, detail="Desactiva primero las asignaciones activas de este rol.")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE rsu_system_role SET activo=0 WHERE system_role_id=%s", (role_id,))
        conn.commit()
        cur.close()
    return {"ok": True, "message": "Rol institucional desactivado."}


def _validate_assignment(
    cur,
    data: AuthorityAssignmentIn,
    *,
    exclude_assignment_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    cur.execute(
        """SELECT
            p.persona_id, p.activo AS persona_activa,
            u.usuario_id, u.username, u.activo AS usuario_activo,
            CASE WHEN d.persona_id IS NOT NULL THEN 'Docente'
                 WHEN a.persona_id IS NOT NULL THEN 'Administrativo'
                 ELSE 'Persona' END AS tipo_persona
        FROM persona p
        LEFT JOIN usuario u ON u.persona_id=p.persona_id
        LEFT JOIN docente d ON d.persona_id=p.persona_id
        LEFT JOIN administrativo a ON a.persona_id=p.persona_id
        WHERE p.persona_id=%s LIMIT 1""",
        (data.persona_id,),
    )
    person = cur.fetchone()
    if not person:
        raise ValueError("La persona seleccionada no existe.")
    if not bool(person["persona_activa"]):
        raise ValueError("La persona seleccionada está inactiva.")
    if person.get("tipo_persona") not in {"Docente", "Administrativo"}:
        raise ValueError("Los cargos institucionales solo pueden asignarse a docentes o administrativos.")

    account_requested = bool(data.crear_o_activar_cuenta)
    username = _clean(data.username)
    password = str(data.password or "")

    if not person.get("usuario_id"):
        if not account_requested:
            raise ValueError(
                "El docente no tiene cuenta de acceso. Activa la opción «Crear cuenta al guardar» "
                "e ingresa un usuario y una contraseña temporal."
            )
        if not username or len(username) < 3:
            raise ValueError("Ingresa un nombre de usuario de al menos 3 caracteres.")
        if not password:
            raise ValueError(PASSWORD_POLICY_MESSAGE)
        validate_password_policy(password)
        cur.execute("SELECT 1 AS ok FROM usuario WHERE LOWER(username)=LOWER(%s) LIMIT 1", (username,))
        if cur.fetchone():
            raise ValueError("El nombre de usuario ya está registrado.")
        user_id = str(uuid4())
        cur.execute(
            """INSERT INTO usuario (usuario_id,persona_id,username,password_hash,activo)
            VALUES (%s,%s,%s,%s,1)""",
            (user_id, data.persona_id, username, hash_password(password)),
        )
        person["usuario_id"] = user_id
        person["username"] = username
        person["usuario_activo"] = 1
    elif not bool(person.get("usuario_activo")):
        if not account_requested:
            raise ValueError(
                "La cuenta del docente está inactiva. Activa la opción «Reactivar cuenta al guardar»."
            )
        update_username = username or str(person.get("username") or "").strip()
        if len(update_username) < 3:
            raise ValueError("Ingresa un nombre de usuario válido para reactivar la cuenta.")
        cur.execute(
            "SELECT usuario_id FROM usuario WHERE LOWER(username)=LOWER(%s) AND usuario_id<>%s LIMIT 1",
            (update_username, person["usuario_id"]),
        )
        if cur.fetchone():
            raise ValueError("El nombre de usuario ya está registrado.")
        if password:
            validate_password_policy(password)
            cur.execute(
                "UPDATE usuario SET username=%s,password_hash=%s,activo=1 WHERE usuario_id=%s",
                (update_username, hash_password(password), person["usuario_id"]),
            )
        else:
            cur.execute(
                "UPDATE usuario SET username=%s,activo=1 WHERE usuario_id=%s",
                (update_username, person["usuario_id"]),
            )
        person["username"] = update_username
        person["usuario_activo"] = 1

    cur.execute("SELECT * FROM rsu_system_role WHERE system_role_id=%s LIMIT 1", (data.system_role_id,))
    role = cur.fetchone()
    if not role or not bool(role["activo"]):
        raise ValueError("El rol institucional seleccionado no existe o está inactivo.")

    code = str(role["codigo"])
    faculty = _clean(data.facultad)
    if code in FACULTY_REQUIRED_ROLES and not faculty:
        raise ValueError("Selecciona la facultad correspondiente al cargo institucional.")
    if data.fecha_inicio and data.fecha_fin and data.fecha_fin < data.fecha_inicio:
        raise ValueError("La fecha de término no puede ser anterior a la fecha de inicio.")

    exclusion = ""
    params: list[Any] = [role["system_role_id"]]
    if exclude_assignment_id:
        exclusion = " AND existing.user_system_role_id<>%s"
        params.append(exclude_assignment_id)

    if data.activo and code in GLOBAL_SINGLETON_ROLES:
        cur.execute(
            f"""SELECT CONCAT(p.nombres,' ',p.apellidos) AS nombre
            FROM rsu_user_system_role existing
            INNER JOIN usuario eu ON eu.usuario_id=existing.usuario_id
            INNER JOIN persona p ON p.persona_id=eu.persona_id
            WHERE existing.system_role_id=%s AND existing.activo=1 {exclusion}
            LIMIT 1""",
            tuple(params),
        )
        occupied = cur.fetchone()
        if occupied:
            raise ValueError(f"El rol ya está asignado a {occupied['nombre']}.")

    if data.activo and code in FACULTY_SINGLETON_ROLES:
        faculty_params = list(params)
        faculty_params.append(faculty)
        cur.execute(
            f"""SELECT CONCAT(p.nombres,' ',p.apellidos) AS nombre
            FROM rsu_user_system_role existing
            INNER JOIN usuario eu ON eu.usuario_id=existing.usuario_id
            INNER JOIN persona p ON p.persona_id=eu.persona_id
            WHERE existing.system_role_id=%s AND existing.activo=1 {exclusion}
              AND LOWER(TRIM(existing.facultad))=LOWER(TRIM(%s))
            LIMIT 1""",
            tuple(faculty_params),
        )
        occupied = cur.fetchone()
        if occupied:
            raise ValueError(f"La facultad seleccionada ya tiene este cargo activo: {occupied['nombre']}.")

    cur.execute(
        f"""SELECT existing.user_system_role_id
        FROM rsu_user_system_role existing
        WHERE existing.usuario_id=%s AND existing.system_role_id=%s
          AND COALESCE(LOWER(TRIM(existing.facultad)),'')=COALESCE(LOWER(TRIM(%s)),'')
          {('AND existing.user_system_role_id<>%s' if exclude_assignment_id else '')}
        LIMIT 1""",
        (
            person["usuario_id"],
            role["system_role_id"],
            faculty,
            *([exclude_assignment_id] if exclude_assignment_id else []),
        ),
    )
    duplicate = cur.fetchone()
    if duplicate:
        raise ValueError("La persona ya tiene este rol asignado para el mismo ámbito.")

    return person, role, faculty


def _save_signature(persona_id: str, encoded: str | None, filename: str | None) -> str | None:
    if not encoded:
        return None
    raw = encoded.split(",", 1)[-1]
    try:
        content = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("La firma institucional no tiene un formato válido.") from exc
    if not content:
        raise ValueError("La firma institucional está vacía.")
    suffix = Path(filename or "firma.png").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("La firma debe estar en formato PNG, JPG, JPEG o WEBP.")
    directory = Path(get_settings().upload_dir) / "authority_signatures"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{persona_id}{suffix}"
    # Elimina versiones anteriores con otra extensión para evitar ambigüedad.
    for candidate in directory.glob(f"{persona_id}.*"):
        if candidate != path:
            candidate.unlink(missing_ok=True)
    path.write_bytes(content)
    return str(path)


def _upsert_profile(cur, data: AuthorityAssignmentIn, faculty: str | None, signature_path: str | None) -> None:
    cur.execute(
        """INSERT INTO rsu_authority_profile
        (persona_id,cargo_oficial,facultad,oficina,resolucion_designacion,fecha_inicio,fecha_fin,firma_url,activo)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          cargo_oficial=VALUES(cargo_oficial),facultad=VALUES(facultad),oficina=VALUES(oficina),
          resolucion_designacion=VALUES(resolucion_designacion),fecha_inicio=VALUES(fecha_inicio),
          fecha_fin=VALUES(fecha_fin),firma_url=COALESCE(VALUES(firma_url),firma_url),activo=VALUES(activo)""",
        (
            data.persona_id,
            data.cargo_oficial.strip(),
            faculty,
            _clean(data.oficina),
            _clean(data.resolucion_designacion),
            data.fecha_inicio,
            data.fecha_fin,
            signature_path,
            int(data.activo),
        ),
    )


@router.post("/asignaciones")
def crear_asignacion(data: AuthorityAssignmentIn, _: AuthUser = Depends(require_admin)):
    assignment_id = str(uuid4())
    try:
        with get_connection() as conn:
            cur = conn.cursor(dictionary=True)
            person, role, faculty = _validate_assignment(cur, data)
            signature_path = _save_signature(data.persona_id, data.firma_base64, data.firma_filename)
            cur.execute(
                """INSERT INTO rsu_user_system_role
                (user_system_role_id,usuario_id,system_role_id,facultad,activo)
                VALUES (%s,%s,%s,%s,%s)""",
                (assignment_id, person["usuario_id"], role["system_role_id"], faculty, int(data.activo)),
            )
            if str(role.get("codigo") or "") not in NON_AUTHORITY_ROLES:
                _upsert_profile(cur, data, faculty, signature_path)
            _refresh_profile_status(cur, data.persona_id)
            conn.commit()
            cur.close()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_friendly_error(exc)) from exc
    return _assignment_query("WHERE usr.user_system_role_id=%s", (assignment_id,))[0]


@router.put("/asignaciones/{assignment_id}")
def actualizar_asignacion(
    assignment_id: str,
    data: AuthorityAssignmentIn,
    _: AuthUser = Depends(require_admin),
):
    current = fetch_one(
        """SELECT usr.*,u.persona_id
        FROM rsu_user_system_role usr
        INNER JOIN usuario u ON u.usuario_id=usr.usuario_id
        WHERE usr.user_system_role_id=%s""",
        (assignment_id,),
    )
    if not current:
        raise HTTPException(status_code=404, detail="Asignación institucional no encontrada.")
    try:
        with get_connection() as conn:
            cur = conn.cursor(dictionary=True)
            person, role, faculty = _validate_assignment(cur, data, exclude_assignment_id=assignment_id)
            signature_path = _save_signature(data.persona_id, data.firma_base64, data.firma_filename)
            cur.execute(
                """UPDATE rsu_user_system_role
                SET usuario_id=%s,system_role_id=%s,facultad=%s,activo=%s
                WHERE user_system_role_id=%s""",
                (person["usuario_id"], role["system_role_id"], faculty, int(data.activo), assignment_id),
            )
            if str(role.get("codigo") or "") not in NON_AUTHORITY_ROLES:
                _upsert_profile(cur, data, faculty, signature_path)
            _refresh_profile_status(cur, data.persona_id)
            if str(current.get("persona_id")) != data.persona_id:
                _refresh_profile_status(cur, str(current.get("persona_id")))
            conn.commit()
            cur.close()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_friendly_error(exc)) from exc
    return _assignment_query("WHERE usr.user_system_role_id=%s", (assignment_id,))[0]


def _refresh_profile_status(cur, persona_id: str) -> None:
    cur.execute(
        """SELECT 1
        FROM rsu_user_system_role usr
        INNER JOIN usuario u ON u.usuario_id=usr.usuario_id
        INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id
        WHERE u.persona_id=%s AND usr.activo=1
          AND sr.codigo NOT IN ('administrativo_normal') LIMIT 1""",
        (persona_id,),
    )
    has_active = cur.fetchone() is not None
    cur.execute("UPDATE rsu_authority_profile SET activo=%s WHERE persona_id=%s", (int(has_active), persona_id))


@router.patch("/asignaciones/{assignment_id}/estado")
def cambiar_estado_asignacion(
    assignment_id: str,
    data: AuthorityAssignmentStatusIn,
    _: AuthUser = Depends(require_admin),
):
    current = fetch_one(
        """SELECT usr.*,u.persona_id,sr.codigo
        FROM rsu_user_system_role usr
        INNER JOIN usuario u ON u.usuario_id=usr.usuario_id
        INNER JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id
        WHERE usr.user_system_role_id=%s""",
        (assignment_id,),
    )
    if not current:
        raise HTTPException(status_code=404, detail="Asignación institucional no encontrada.")
    if data.activo:
        profile = fetch_one(
            """SELECT cargo_oficial,oficina,resolucion_designacion,fecha_inicio,fecha_fin
            FROM rsu_authority_profile WHERE persona_id=%s""",
            (current["persona_id"],),
        ) or {}
        payload = AuthorityAssignmentIn(
            persona_id=str(current["persona_id"]),
            system_role_id=str(current["system_role_id"]),
            facultad=current.get("facultad"),
            cargo_oficial=str(profile.get("cargo_oficial") or "Autoridad universitaria"),
            oficina=profile.get("oficina"),
            resolucion_designacion=profile.get("resolucion_designacion"),
            fecha_inicio=profile.get("fecha_inicio"),
            fecha_fin=profile.get("fecha_fin"),
            activo=True,
        )
        try:
            with get_connection() as conn:
                cur = conn.cursor(dictionary=True)
                _validate_assignment(cur, payload, exclude_assignment_id=assignment_id)
                cur.execute("UPDATE rsu_user_system_role SET activo=1 WHERE user_system_role_id=%s", (assignment_id,))
                cur.execute("UPDATE rsu_authority_profile SET activo=1 WHERE persona_id=%s", (current["persona_id"],))
                conn.commit()
                cur.close()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        with get_connection() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute("UPDATE rsu_user_system_role SET activo=0 WHERE user_system_role_id=%s", (assignment_id,))
            _refresh_profile_status(cur, str(current["persona_id"]))
            conn.commit()
            cur.close()
    return {"ok": True, "activo": data.activo}


@router.delete("/asignaciones/{assignment_id}")
def desactivar_asignacion(assignment_id: str, _: AuthUser = Depends(require_admin)):
    return cambiar_estado_asignacion(assignment_id, AuthorityAssignmentStatusIn(activo=False), _)


@router.get("/firmas/{persona_id}")
def descargar_firma(persona_id: str, _: AuthUser = Depends(require_admin)):
    row = fetch_one("SELECT firma_url FROM rsu_authority_profile WHERE persona_id=%s", (persona_id,))
    if not row or not _clean(row.get("firma_url")):
        raise HTTPException(status_code=404, detail="La autoridad no tiene una firma registrada.")
    path = Path(str(row["firma_url"]))
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="El archivo de firma no está disponible en el servidor.")
    return FileResponse(path, filename=path.name)


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()
    if "uq_rsu_system_role_code" in lower or "duplicate entry" in lower and "codigo" in lower:
        return "Ya existe un rol institucional con ese código."
    if "uq_user_system_role" in lower:
        return "La persona ya tiene este rol asignado para el mismo ámbito."
    return text
