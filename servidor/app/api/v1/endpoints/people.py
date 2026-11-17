from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user, require_admin
from app.core.config import get_settings
from app.core.password_policy import PASSWORD_POLICY_MESSAGE, validate_password_policy
from app.core.security import hash_password
from app.db.mysql import fetch_all, fetch_one, get_connection
from app.schemas.auth import AuthUser
from app.schemas.people import (
    AdministrativoRegistroIn,
    DocenteRegistroIn,
    EstudianteRegistroIn,
    PersonaContactoIn,
    PersonaEstadoIn,
    PersonaGestionUpdateIn,
    PersonaRegistroBase,
    VinculadoRegistroIn,
)

router = APIRouter(prefix="/personas", tags=["Personas y contactos"])


def _persona_query(where: str) -> str:
    return f"""
        SELECT
            p.persona_id, p.nombres, p.apellidos, p.dni, p.activo, p.fecha_registro,
            c.contacto_id, c.email_personal, c.email_institucional, c.telefono, c.direccion,
            u.usuario_id, u.username, u.activo AS usuario_activo,
            CASE
                WHEN d.persona_id IS NOT NULL THEN 'Docente'
                WHEN e.persona_id IS NOT NULL THEN 'Estudiante'
                WHEN af.tipo_vinculo = 'no_docente' THEN 'No docente'
                WHEN af.tipo_vinculo = 'egresado' THEN 'Egresado'
                WHEN af.tipo_vinculo = 'graduado' THEN 'Graduado'
                WHEN a.persona_id IS NOT NULL THEN 'Administrativo'
                ELSE 'Persona'
            END AS tipo_persona,
            d.codigo_docente, d.departamento_academico_id AS docente_departamento_id,
            dd.facultad AS docente_facultad, dd.carrera AS docente_carrera,
            d.especialidad, d.condicion, d.categoria, d.regimen_dedicacion,
            e.codigo_estudiante, e.departamento_academico_id AS estudiante_departamento_id,
            de.facultad AS estudiante_facultad, de.carrera AS estudiante_carrera,
            e.es_becado,
            a.area, a.cargo,
            af.tipo_vinculo, af.departamento_academico_id AS vinculado_departamento_id,
            dav.facultad AS vinculado_facultad, dav.carrera AS vinculado_carrera,
            af.codigo_institucional, af.anio_egreso, af.grado_academico, af.institucion_origen,
            sr.codigo AS rol_sistema, usr.facultad AS facultad_asignada,
            ap.cargo_oficial, ap.oficina, ap.resolucion_designacion,
            ap.fecha_inicio AS fecha_inicio_designacion, ap.fecha_fin AS fecha_fin_designacion,
            ap.firma_url
        FROM persona p
        LEFT JOIN contacto c ON c.persona_id = p.persona_id
        LEFT JOIN usuario u ON u.persona_id = p.persona_id
        LEFT JOIN docente d ON d.persona_id = p.persona_id
        LEFT JOIN departamento_academico dd
            ON dd.departamento_academico_id = d.departamento_academico_id
        LEFT JOIN estudiante e ON e.persona_id = p.persona_id
        LEFT JOIN departamento_academico de
            ON de.departamento_academico_id = e.departamento_academico_id
        LEFT JOIN administrativo a ON a.persona_id = p.persona_id
        LEFT JOIN rsu_person_affiliation af ON af.persona_id=p.persona_id
        LEFT JOIN departamento_academico dav ON dav.departamento_academico_id=af.departamento_academico_id
        LEFT JOIN rsu_user_system_role usr ON usr.usuario_id=u.usuario_id AND usr.activo=1
        LEFT JOIN rsu_system_role sr ON sr.system_role_id=usr.system_role_id AND sr.activo=1
        LEFT JOIN rsu_authority_profile ap ON ap.persona_id=p.persona_id AND ap.activo=1
        {where}
    """


@router.get("")
def listar_personas(
    q: str | None = None,
    tipo: Literal["administrativo", "docente", "estudiante", "no_docente", "egresado", "graduado"] | None = None,
    _: AuthUser = Depends(require_admin),
):
    conditions: list[str] = []
    params: list[str] = []

    if q and q.strip():
        conditions.append(
            """
            (
                p.nombres LIKE %s OR p.apellidos LIKE %s OR p.dni LIKE %s
                OR c.email_personal LIKE %s OR c.email_institucional LIKE %s
                OR c.telefono LIKE %s OR u.username LIKE %s
                OR d.codigo_docente LIKE %s OR e.codigo_estudiante LIKE %s
                OR a.area LIKE %s OR a.cargo LIKE %s
            )
            """
        )
        like = f"%{q.strip()}%"
        params.extend([like] * 11)

    if tipo == "administrativo":
        conditions.append("a.persona_id IS NOT NULL")
    elif tipo == "docente":
        conditions.append("d.persona_id IS NOT NULL")
    elif tipo == "estudiante":
        conditions.append("e.persona_id IS NOT NULL")
    elif tipo in {"no_docente", "egresado", "graduado"}:
        conditions.append("af.tipo_vinculo = %s")
        params.append(tipo)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return fetch_all(
        _persona_query(where) + " ORDER BY p.apellidos, p.nombres LIMIT 1000",
        tuple(params),
    )


@router.get("/{persona_id}")
def obtener_persona(persona_id: str, _: AuthUser = Depends(require_admin)):
    row = fetch_one(_persona_query("WHERE p.persona_id = %s") + " LIMIT 1", (persona_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Persona no encontrada.")
    return row


@router.post("")
def crear_persona(data: PersonaContactoIn, _: AuthUser = Depends(require_admin)):
    """Mantiene compatibilidad con el formulario antiguo de persona/contacto."""
    persona_id = str(uuid4())
    contacto_id = str(uuid4())
    with get_connection() as conn:
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO persona (persona_id, nombres, apellidos, dni, activo)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (persona_id, data.nombres, data.apellidos, data.dni, int(data.activo)),
            )
            cur.execute(
                """
                INSERT INTO contacto
                (contacto_id, persona_id, email_personal, email_institucional, telefono, direccion)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (contacto_id, persona_id, data.email_personal, data.email_institucional, data.telefono, data.direccion),
            )
            conn.commit()
            cur.close()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise HTTPException(status_code=400, detail=_friendly_db_error(exc)) from exc
    return obtener_persona(persona_id)


@router.post("/registrar/administrativo")
def registrar_administrativo(data: AdministrativoRegistroIn, _: AuthUser = Depends(require_admin)):
    return _registrar_persona(data, "administrativo")


@router.post("/registrar/docente")
def registrar_docente(data: DocenteRegistroIn, _: AuthUser = Depends(require_admin)):
    return _registrar_persona(data, "docente")


@router.post("/registrar/estudiante")
def registrar_estudiante(data: EstudianteRegistroIn, _: AuthUser = Depends(require_admin)):
    return _registrar_persona(data, "estudiante")


@router.post("/registrar/vinculado")
def registrar_vinculado(data: VinculadoRegistroIn, _: AuthUser = Depends(require_admin)):
    return _registrar_persona(data, "vinculado")


def _registrar_persona(
    data: AdministrativoRegistroIn | DocenteRegistroIn | EstudianteRegistroIn | VinculadoRegistroIn,
    tipo: Literal["administrativo", "docente", "estudiante", "vinculado"],
) -> dict[str, Any]:
    _validate_registration(data)
    persona_id = str(uuid4())
    contacto_id = str(uuid4())
    usuario_id = str(uuid4()) if data.crear_cuenta else None

    with get_connection() as conn:
        try:
            cur = conn.cursor()
            _ensure_unique_identifiers(
                cur,
                dni=data.dni,
                student_code=data.codigo_estudiante if isinstance(data, EstudianteRegistroIn) else None,
            )

            if tipo in {"docente", "estudiante"}:
                departamento_id = data.departamento_academico_id  # type: ignore[attr-defined]
                _validate_department(cur, departamento_id)
            elif tipo == "vinculado" and data.departamento_academico_id:  # type: ignore[attr-defined]
                _validate_department(cur, data.departamento_academico_id)  # type: ignore[attr-defined]

            cur.execute(
                """
                INSERT INTO persona (persona_id, nombres, apellidos, dni, activo)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (persona_id, data.nombres.strip(), data.apellidos.strip(), data.dni, int(data.activo)),
            )

            if _has_contact(data):
                cur.execute(
                    """
                    INSERT INTO contacto
                    (contacto_id, persona_id, email_personal, email_institucional, telefono, direccion)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        contacto_id,
                        persona_id,
                        _clean(data.email_personal),
                        _clean(data.email_institucional),
                        _clean(data.telefono),
                        _clean(data.direccion),
                    ),
                )

            if tipo == "administrativo":
                assert isinstance(data, AdministrativoRegistroIn)
                if not _clean(data.area):
                    raise ValueError("El área administrativa es obligatoria.")
                if not _clean(data.cargo):
                    raise ValueError("El cargo administrativo es obligatorio.")
                cur.execute(
                    """
                    INSERT INTO administrativo (persona_id, area, cargo)
                    VALUES (%s, %s, %s)
                    """,
                    (persona_id, _clean(data.area), _clean(data.cargo)),
                )
            elif tipo == "docente":
                assert isinstance(data, DocenteRegistroIn)
                condition, category, dedication = _validate_teacher_choices(
                    data.condicion,
                    data.categoria,
                    data.regimen_dedicacion,
                )
                cur.execute(
                    """
                    INSERT INTO docente
                    (persona_id, codigo_docente, departamento_academico_id, especialidad,
                     condicion, categoria, regimen_dedicacion)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        persona_id,
                        data.codigo_docente.strip(),
                        data.departamento_academico_id,
                        _clean(data.especialidad),
                        condition,
                        category,
                        dedication,
                    ),
                )
            elif tipo == "estudiante":
                assert isinstance(data, EstudianteRegistroIn)
                cur.execute(
                    """
                    INSERT INTO estudiante
                    (persona_id, codigo_estudiante, departamento_academico_id, es_becado)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        persona_id,
                        data.codigo_estudiante.strip(),
                        data.departamento_academico_id,
                        int(data.es_becado),
                    ),
                )

            if data.crear_cuenta:
                cur.execute(
                    """
                    INSERT INTO usuario
                    (usuario_id, persona_id, username, password_hash, activo)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        usuario_id,
                        persona_id,
                        str(data.username).strip(),
                        hash_password(str(data.password)),
                        int(data.activo),
                    ),
                )

            if tipo == "administrativo" and usuario_id:
                # El registro de personal solo crea el acceso base. Los cargos,
                # resoluciones, vigencias y firmas se asignan posteriormente en
                # Configuración > Roles y autoridades.
                cur.execute(
                    "SELECT system_role_id FROM rsu_system_role WHERE codigo='administrativo_normal' AND activo=1 LIMIT 1"
                )
                role_row = cur.fetchone()
                if not role_row:
                    raise ValueError("No existe el rol base Administrativo general.")
                system_role_id = role_row["system_role_id"] if isinstance(role_row, dict) else role_row[0]
                cur.execute(
                    """INSERT INTO rsu_user_system_role
                    (user_system_role_id,usuario_id,system_role_id,facultad,activo)
                    VALUES (%s,%s,%s,NULL,1)""",
                    (str(uuid4()), usuario_id, system_role_id),
                )

            if tipo == "vinculado":
                assert isinstance(data, VinculadoRegistroIn)
                cur.execute(
                    """INSERT INTO rsu_person_affiliation
                    (persona_id,tipo_vinculo,departamento_academico_id,codigo_institucional,anio_egreso,grado_academico,institucion_origen)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        persona_id,
                        data.tipo_vinculo,
                        data.departamento_academico_id,
                        _clean(data.codigo_institucional),
                        data.anio_egreso,
                        _clean(data.grado_academico),
                        _clean(data.institucion_origen),
                    ),
                )

            conn.commit()
            cur.close()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise HTTPException(status_code=400, detail=_friendly_db_error(exc)) from exc

    row = fetch_one(_persona_query("WHERE p.persona_id = %s") + " LIMIT 1", (persona_id,))
    if row is None:
        raise HTTPException(status_code=500, detail="La persona se registró, pero no pudo recuperarse.")
    return row


def _validate_registration(data: PersonaRegistroBase) -> None:
    _validate_emails(data.email_personal, data.email_institucional)

    if data.crear_cuenta:
        if not _clean(data.username):
            raise HTTPException(status_code=422, detail="El nombre de usuario es obligatorio al crear una cuenta.")
        if not data.password:
            raise HTTPException(status_code=422, detail=PASSWORD_POLICY_MESSAGE)
        try:
            validate_password_policy(data.password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


def _ensure_unique_identifiers(
    cur,
    *,
    dni: str,
    student_code: str | None = None,
    exclude_person_id: str | None = None,
) -> None:
    params: list[Any] = [str(dni).strip()]
    exclusion = ""
    if exclude_person_id:
        exclusion = " AND persona_id<>%s"
        params.append(exclude_person_id)
    cur.execute(
        f"SELECT persona_id FROM persona WHERE dni=%s{exclusion} LIMIT 1",
        tuple(params),
    )
    if cur.fetchone():
        raise ValueError("Ya existe una persona registrada con ese DNI.")

    clean_code = str(student_code or "").strip()
    if clean_code:
        params = [clean_code]
        exclusion = ""
        if exclude_person_id:
            exclusion = " AND persona_id<>%s"
            params.append(exclude_person_id)
        cur.execute(
            f"SELECT persona_id FROM estudiante WHERE codigo_estudiante=%s{exclusion} LIMIT 1",
            tuple(params),
        )
        if cur.fetchone():
            raise ValueError("Ya existe un estudiante registrado con ese código estudiantil.")


def _has_contact(data: PersonaRegistroBase | PersonaGestionUpdateIn) -> bool:
    return any(
        _clean(value)
        for value in (data.email_personal, data.email_institucional, data.telefono, data.direccion)
    )


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None


def _save_authority_signature(persona_id: str, encoded: str | None, filename: str | None) -> str | None:
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
    path.write_bytes(content)
    return str(path)


def _validate_emails(email_personal: str | None, email_institucional: str | None) -> None:
    for label, email in (
        ("correo personal", email_personal),
        ("correo institucional", email_institucional),
    ):
        clean_email = _clean(email)
        if clean_email and ("@" not in clean_email or clean_email.startswith("@") or clean_email.endswith("@")):
            raise HTTPException(status_code=422, detail=f"El {label} no tiene un formato válido.")


def _validate_department(cur, department_id: str | None) -> None:
    if not department_id:
        raise ValueError("Selecciona un departamento académico válido.")
    cur.execute(
        """
        SELECT departamento_academico_id
        FROM departamento_academico
        WHERE departamento_academico_id = %s AND activo = 1
        """,
        (department_id,),
    )
    if cur.fetchone() is None:
        raise ValueError("El departamento académico seleccionado no existe o está inactivo.")


def _validate_teacher_choices(
    condicion: str | None,
    categoria: str | None,
    regimen_dedicacion: str | None,
) -> tuple[str, str, str]:
    condition = str(condicion or "").strip().title()
    category = str(categoria or "").strip().title()
    raw_dedication = str(regimen_dedicacion or "").strip()
    dedication_aliases = {
        "tc": "Tiempo completo",
        "tiempo completo": "Tiempo completo",
        "tp": "Tiempo parcial",
        "tiempo parcial": "Tiempo parcial",
    }
    dedication = dedication_aliases.get(raw_dedication.lower(), raw_dedication)
    if condition not in {"Ordinario", "Contratado"}:
        raise ValueError("La condición del docente debe ser Ordinario o Contratado.")
    if category not in {"Auxiliar", "Asociado", "Principal"}:
        raise ValueError("La categoría del docente debe ser Auxiliar, Asociado o Principal.")
    if dedication not in {"Tiempo completo", "Tiempo parcial"}:
        raise ValueError("El régimen de dedicación debe ser Tiempo completo o Tiempo parcial.")
    return condition, category, dedication


def _friendly_db_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "uq_persona_dni" in lowered or "persona.dni" in lowered:
        return "Ya existe una persona registrada con ese DNI."
    if "uq_usuario_username" in lowered or "usuario.username" in lowered:
        return "El nombre de usuario ya está registrado."
    if "uq_docente_codigo" in lowered or "codigo_docente" in lowered:
        return "El código de docente ya está registrado."
    if "uq_estudiante_codigo" in lowered or "codigo_estudiante" in lowered:
        return "El código de estudiante ya está registrado."
    if "uq_contacto_email" in lowered or "email_institucional" in lowered:
        return "El correo institucional ya está registrado."
    return message


@router.put("/{persona_id}")
def actualizar_persona(
    persona_id: str,
    data: PersonaGestionUpdateIn,
    _: AuthUser = Depends(require_admin),
):
    existing = fetch_one(_persona_query("WHERE p.persona_id = %s") + " LIMIT 1", (persona_id,))
    if existing is None:
        raise HTTPException(status_code=404, detail="Persona no encontrada.")

    target_type = data.tipo_persona.strip().lower()
    _validate_emails(data.email_personal, data.email_institucional)
    if data.password:
        try:
            validate_password_policy(data.password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    with get_connection() as conn:
        try:
            cur = conn.cursor()
            _ensure_unique_identifiers(
                cur,
                dni=data.dni,
                student_code=data.codigo_estudiante if target_type == "estudiante" else None,
                exclude_person_id=persona_id,
            )

            if target_type in {"docente", "estudiante"}:
                _validate_department(cur, data.departamento_academico_id)

            cur.execute(
                """
                UPDATE persona
                SET nombres=%s, apellidos=%s, dni=%s, activo=%s
                WHERE persona_id=%s
                """,
                (
                    data.nombres.strip(),
                    data.apellidos.strip(),
                    data.dni,
                    int(data.activo),
                    persona_id,
                ),
            )

            cur.execute("SELECT contacto_id FROM contacto WHERE persona_id=%s", (persona_id,))
            contact_exists = cur.fetchone()
            contact_values = (
                _clean(data.email_personal),
                _clean(data.email_institucional),
                _clean(data.telefono),
                _clean(data.direccion),
            )
            if contact_exists and any(contact_values):
                cur.execute(
                    """
                    UPDATE contacto
                    SET email_personal=%s, email_institucional=%s, telefono=%s, direccion=%s
                    WHERE persona_id=%s
                    """,
                    (*contact_values, persona_id),
                )
            elif contact_exists:
                cur.execute("DELETE FROM contacto WHERE persona_id=%s", (persona_id,))
            elif any(contact_values):
                cur.execute(
                    """
                    INSERT INTO contacto
                    (contacto_id, persona_id, email_personal, email_institucional, telefono, direccion)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (str(uuid4()), persona_id, *contact_values),
                )

            _upsert_person_type(cur, persona_id, target_type, data)
            _remove_other_person_types(cur, persona_id, target_type)

            cur.execute(
                "SELECT usuario_id, username FROM usuario WHERE persona_id=%s",
                (persona_id,),
            )
            user_row = cur.fetchone()
            desired_user_active = int(data.activo and data.usuario_activo and data.crear_cuenta)

            if user_row:
                if data.crear_cuenta:
                    username = _clean(data.username) or user_row[1]
                    if not username or len(username) < 3:
                        raise ValueError("El nombre de usuario debe tener al menos 3 caracteres.")
                    if data.password:
                        cur.execute(
                            """
                            UPDATE usuario
                            SET username=%s, password_hash=%s, activo=%s
                            WHERE persona_id=%s
                            """,
                            (username, hash_password(data.password), desired_user_active, persona_id),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE usuario SET username=%s, activo=%s WHERE persona_id=%s
                            """,
                            (username, desired_user_active, persona_id),
                        )
                else:
                    cur.execute("UPDATE usuario SET activo=0 WHERE persona_id=%s", (persona_id,))
            elif data.crear_cuenta:
                username = _clean(data.username)
                if not username or len(username) < 3:
                    raise ValueError("El nombre de usuario debe tener al menos 3 caracteres.")
                if not data.password:
                    raise ValueError(PASSWORD_POLICY_MESSAGE)
                validate_password_policy(data.password)
                cur.execute(
                    """
                    INSERT INTO usuario
                    (usuario_id, persona_id, username, password_hash, activo)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid4()),
                        persona_id,
                        username,
                        hash_password(data.password),
                        desired_user_active,
                    ),
                )

            conn.commit()
            cur.close()
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise HTTPException(status_code=400, detail=_friendly_db_error(exc)) from exc

    row = fetch_one(_persona_query("WHERE p.persona_id = %s") + " LIMIT 1", (persona_id,))
    if row is None:
        raise HTTPException(status_code=500, detail="La persona se actualizó, pero no pudo recuperarse.")
    return row


def _upsert_person_type(cur, persona_id: str, target_type: str, data: PersonaGestionUpdateIn) -> None:
    if target_type == "administrativo":
        cur.execute(
            """
            INSERT INTO administrativo (persona_id, area, cargo)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE area=VALUES(area), cargo=VALUES(cargo)
            """,
            (persona_id, _clean(data.area), _clean(data.cargo)),
        )
        return

    if target_type == "docente":
        if not _clean(data.codigo_docente):
            raise ValueError("El código de docente es obligatorio.")
        condition, category, dedication = _validate_teacher_choices(
            data.condicion,
            data.categoria,
            data.regimen_dedicacion,
        )
        cur.execute(
            """
            INSERT INTO docente
            (persona_id, codigo_docente, departamento_academico_id, especialidad,
             condicion, categoria, regimen_dedicacion)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              codigo_docente=VALUES(codigo_docente),
              departamento_academico_id=VALUES(departamento_academico_id),
              especialidad=VALUES(especialidad),
              condicion=VALUES(condicion),
              categoria=VALUES(categoria),
              regimen_dedicacion=VALUES(regimen_dedicacion)
            """,
            (
                persona_id,
                str(data.codigo_docente).strip(),
                data.departamento_academico_id,
                _clean(data.especialidad),
                condition,
                category,
                dedication,
            ),
        )
        return

    if target_type == "estudiante":
        if not _clean(data.codigo_estudiante):
            raise ValueError("El código de estudiante es obligatorio.")
        cur.execute(
            """
            INSERT INTO estudiante
            (persona_id, codigo_estudiante, departamento_academico_id, es_becado)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              codigo_estudiante=VALUES(codigo_estudiante),
              departamento_academico_id=VALUES(departamento_academico_id),
              es_becado=VALUES(es_becado)
            """,
            (
                persona_id,
                str(data.codigo_estudiante).strip(),
                data.departamento_academico_id,
                int(data.es_becado),
            ),
        )
        return

    raise ValueError("El tipo de persona seleccionado no es válido.")


def _remove_other_person_types(cur, persona_id: str, target_type: str) -> None:
    table_by_type = {
        "administrativo": "administrativo",
        "docente": "docente",
        "estudiante": "estudiante",
    }
    for person_type, table in table_by_type.items():
        if person_type != target_type:
            cur.execute(f"DELETE FROM {table} WHERE persona_id=%s", (persona_id,))


@router.patch("/{persona_id}/estado")
def cambiar_estado_persona(
    persona_id: str,
    data: PersonaEstadoIn,
    current_user: AuthUser = Depends(require_admin),
):
    if not data.activo and current_user.persona_id == persona_id:
        raise HTTPException(status_code=400, detail="No puedes desactivar tu propia cuenta mientras la estás utilizando.")
    existing = fetch_one("SELECT persona_id FROM persona WHERE persona_id=%s", (persona_id,))
    if existing is None:
        raise HTTPException(status_code=404, detail="Persona no encontrada.")
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE persona SET activo=%s WHERE persona_id=%s", (int(data.activo), persona_id))
            cur.execute("UPDATE usuario SET activo=%s WHERE persona_id=%s", (int(data.activo), persona_id))
            conn.commit()
            cur.close()
        action = "activadas" if data.activo else "desactivadas"
        return {"ok": True, "message": f"Persona y cuenta de acceso {action}."}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_friendly_db_error(exc)) from exc


@router.delete("/{persona_id}")
def desactivar_persona(persona_id: str, current_user: AuthUser = Depends(require_admin)):
    return cambiar_estado_persona(persona_id, PersonaEstadoIn(activo=False), current_user)


@router.delete("/{persona_id}/permanente")
def eliminar_persona_permanentemente(persona_id: str, current_user: AuthUser = Depends(require_admin)):
    if current_user.persona_id == persona_id:
        raise HTTPException(status_code=400, detail="No puedes eliminar tu propia cuenta mientras la estás utilizando.")
    existing = fetch_one("SELECT persona_id FROM persona WHERE persona_id=%s", (persona_id,))
    if existing is None:
        raise HTTPException(status_code=404, detail="Persona no encontrada.")

    dependencies = (
        ("participaciones en proyectos", "rol_proyecto", "persona_id"),
        ("controles de participación semestral", "participacion_semestre", "persona_id"),
        ("documentos subidos", "documento", "subido_por"),
        ("certificados", "certificado", "persona_id"),
        ("sanciones", "sancion", "persona_id"),
        ("evaluaciones realizadas", "evaluacion_proyecto", "evaluador_persona_id"),
    )

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            related: list[str] = []
            for label, table, column in dependencies:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_schema=DATABASE() AND table_name=%s
                    """,
                    (table,),
                )
                if int(cur.fetchone()[0] or 0) == 0:
                    continue
                cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=%s", (persona_id,))
                if int(cur.fetchone()[0] or 0) > 0:
                    related.append(label)

            if related:
                raise ValueError(
                    "No se puede eliminar definitivamente porque la persona tiene "
                    + ", ".join(related)
                    + ". Desactívala para conservar la trazabilidad institucional."
                )

            cur.execute("DELETE FROM usuario WHERE persona_id=%s", (persona_id,))
            cur.execute("DELETE FROM administrativo WHERE persona_id=%s", (persona_id,))
            cur.execute("DELETE FROM docente WHERE persona_id=%s", (persona_id,))
            cur.execute("DELETE FROM estudiante WHERE persona_id=%s", (persona_id,))
            cur.execute("DELETE FROM contacto WHERE persona_id=%s", (persona_id,))
            cur.execute("DELETE FROM persona WHERE persona_id=%s", (persona_id,))
            conn.commit()
            cur.close()
        return {"ok": True, "message": "Persona eliminada definitivamente."}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_friendly_db_error(exc)) from exc
