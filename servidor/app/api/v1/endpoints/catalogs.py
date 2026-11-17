from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user, require_admin
from app.db.mysql import DatabaseError, execute, fetch_all, fetch_one
from app.schemas.auth import AuthUser
from app.schemas.catalogs import (
    CatalogoRolIn,
    DepartamentoAcademicoIn,
    LineaAccionIn,
    LimiteRolTipoIn,
    PeriodoAcademicoIn,
    TipoDocumentoIn,
    TipoProyectoIn,
)

router = APIRouter(prefix="/catalogos", tags=["Catálogos"])


def _handle_db_error(exc: DatabaseError) -> None:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/estados")
def listar_estados(entidad: str | None = None, _: AuthUser = Depends(get_current_user)):
    if entidad:
        return fetch_all("SELECT * FROM catalogo_estado WHERE entidad = %s ORDER BY nombre", (entidad,))
    return fetch_all("SELECT * FROM catalogo_estado ORDER BY entidad, nombre")


@router.get("/lineas-accion")
def listar_lineas(_: AuthUser = Depends(get_current_user)):
    return fetch_all(
        """
        SELECT *
        FROM linea_accion
        ORDER BY activo DESC,
                 CASE
                     WHEN codigo REGEXP '[0-9]+' THEN CAST(REGEXP_SUBSTR(codigo, '[0-9]+') AS UNSIGNED)
                     ELSE 999999
                 END ASC,
                 codigo ASC,
                 nombre ASC
        """
    )


@router.post("/lineas-accion")
def crear_linea(data: LineaAccionIn, _: AuthUser = Depends(require_admin)):
    item_id = str(uuid4())
    try:
        execute(
            """
            INSERT INTO linea_accion (linea_accion_id, codigo, nombre, descripcion, activo)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (item_id, data.codigo, data.nombre, data.descripcion, int(data.activo)),
        )
        return fetch_one("SELECT * FROM linea_accion WHERE linea_accion_id = %s", (item_id,))
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.put("/lineas-accion/{item_id}")
def actualizar_linea(item_id: str, data: LineaAccionIn, _: AuthUser = Depends(require_admin)):
    try:
        execute(
            """
            UPDATE linea_accion SET codigo=%s, nombre=%s, descripcion=%s, activo=%s
            WHERE linea_accion_id=%s
            """,
            (data.codigo, data.nombre, data.descripcion, int(data.activo), item_id),
        )
        return fetch_one("SELECT * FROM linea_accion WHERE linea_accion_id = %s", (item_id,))
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.delete("/lineas-accion/{item_id}")
def desactivar_linea(item_id: str, _: AuthUser = Depends(require_admin)):
    execute("UPDATE linea_accion SET activo = 0 WHERE linea_accion_id = %s", (item_id,))
    return {"ok": True, "message": "Línea de acción desactivada."}


@router.get("/tipos-proyecto")
def listar_tipos_proyecto(_: AuthUser = Depends(get_current_user)):
    return fetch_all("SELECT * FROM tipo_proyecto ORDER BY nombre")


@router.post("/tipos-proyecto")
def crear_tipo_proyecto(data: TipoProyectoIn, _: AuthUser = Depends(require_admin)):
    item_id = str(uuid4())
    try:
        execute(
            """
            INSERT INTO tipo_proyecto (tipo_proyecto_id, nombre, horas_minimas, horas_jornada_minima, descripcion)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (item_id, data.nombre, data.horas_minimas, data.horas_jornada_minima, data.descripcion),
        )
        return fetch_one("SELECT * FROM tipo_proyecto WHERE tipo_proyecto_id = %s", (item_id,))
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.put("/tipos-proyecto/{item_id}")
def actualizar_tipo_proyecto(item_id: str, data: TipoProyectoIn, _: AuthUser = Depends(require_admin)):
    try:
        execute(
            """
            UPDATE tipo_proyecto
            SET nombre=%s, horas_minimas=%s, horas_jornada_minima=%s, descripcion=%s
            WHERE tipo_proyecto_id=%s
            """,
            (data.nombre, data.horas_minimas, data.horas_jornada_minima, data.descripcion, item_id),
        )
        return fetch_one("SELECT * FROM tipo_proyecto WHERE tipo_proyecto_id = %s", (item_id,))
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.delete("/tipos-proyecto/{item_id}")
def eliminar_tipo_proyecto(item_id: str, _: AuthUser = Depends(require_admin)):
    try:
        execute("DELETE FROM tipo_proyecto WHERE tipo_proyecto_id = %s", (item_id,))
        return {"ok": True, "message": "Tipo de proyecto eliminado."}
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.get("/roles-proyecto")
def listar_roles(_: AuthUser = Depends(get_current_user)):
    return fetch_all("SELECT * FROM catalogo_rol ORDER BY nombre")


@router.post("/roles-proyecto")
def crear_rol(data: CatalogoRolIn, _: AuthUser = Depends(require_admin)):
    item_id = str(uuid4())
    try:
        execute(
            """
            INSERT INTO catalogo_rol (rol_id, nombre, descripcion, solo_docente, solo_voluntariado, activo)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (item_id, data.nombre, data.descripcion, int(data.solo_docente), int(data.solo_voluntariado), int(data.activo)),
        )
        return fetch_one("SELECT * FROM catalogo_rol WHERE rol_id = %s", (item_id,))
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.put("/roles-proyecto/{item_id}")
def actualizar_rol(item_id: str, data: CatalogoRolIn, _: AuthUser = Depends(require_admin)):
    try:
        execute(
            """
            UPDATE catalogo_rol
            SET nombre=%s, descripcion=%s, solo_docente=%s, solo_voluntariado=%s, activo=%s
            WHERE rol_id=%s
            """,
            (data.nombre, data.descripcion, int(data.solo_docente), int(data.solo_voluntariado), int(data.activo), item_id),
        )
        return fetch_one("SELECT * FROM catalogo_rol WHERE rol_id = %s", (item_id,))
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.delete("/roles-proyecto/{item_id}")
def desactivar_rol(item_id: str, _: AuthUser = Depends(require_admin)):
    execute("UPDATE catalogo_rol SET activo = 0 WHERE rol_id = %s", (item_id,))
    return {"ok": True, "message": "Rol desactivado."}


@router.get("/tipos-documento")
def listar_tipos_documento(tipo_proyecto_id: str | None = None, _: AuthUser = Depends(get_current_user)):
    if tipo_proyecto_id:
        return fetch_all(
            "SELECT * FROM tipo_documento WHERE tipo_proyecto_id = %s ORDER BY etapa, anexo_referencia",
            (tipo_proyecto_id,),
        )
    return fetch_all("SELECT * FROM tipo_documento ORDER BY etapa, anexo_referencia")


@router.post("/tipos-documento")
def crear_tipo_documento(data: TipoDocumentoIn, _: AuthUser = Depends(require_admin)):
    item_id = str(uuid4())
    try:
        execute(
            """
            INSERT INTO tipo_documento
            (tipo_documento_id, tipo_proyecto_id, nombre, anexo_referencia, etapa, obligatorio, descripcion)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (item_id, data.tipo_proyecto_id, data.nombre, data.anexo_referencia, data.etapa, int(data.obligatorio), data.descripcion),
        )
        return fetch_one("SELECT * FROM tipo_documento WHERE tipo_documento_id = %s", (item_id,))
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.put("/tipos-documento/{item_id}")
def actualizar_tipo_documento(item_id: str, data: TipoDocumentoIn, _: AuthUser = Depends(require_admin)):
    try:
        execute(
            """
            UPDATE tipo_documento
            SET tipo_proyecto_id=%s, nombre=%s, anexo_referencia=%s, etapa=%s, obligatorio=%s, descripcion=%s
            WHERE tipo_documento_id=%s
            """,
            (data.tipo_proyecto_id, data.nombre, data.anexo_referencia, data.etapa, int(data.obligatorio), data.descripcion, item_id),
        )
        return fetch_one("SELECT * FROM tipo_documento WHERE tipo_documento_id = %s", (item_id,))
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.delete("/tipos-documento/{item_id}")
def eliminar_tipo_documento(item_id: str, _: AuthUser = Depends(require_admin)):
    try:
        execute("DELETE FROM tipo_documento WHERE tipo_documento_id = %s", (item_id,))
        return {"ok": True, "message": "Tipo de documento eliminado."}
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.get("/departamentos-academicos")
def listar_departamentos(_: AuthUser = Depends(get_current_user)):
    return fetch_all("SELECT * FROM departamento_academico ORDER BY facultad, carrera")


@router.post("/departamentos-academicos")
def crear_departamento(data: DepartamentoAcademicoIn, _: AuthUser = Depends(require_admin)):
    item_id = str(uuid4())
    try:
        execute(
            """
            INSERT INTO departamento_academico (departamento_academico_id, facultad, carrera, codigo, activo)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (item_id, data.facultad, data.carrera, data.codigo, int(data.activo)),
        )
        return fetch_one("SELECT * FROM departamento_academico WHERE departamento_academico_id = %s", (item_id,))
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.put("/departamentos-academicos/{item_id}")
def actualizar_departamento(item_id: str, data: DepartamentoAcademicoIn, _: AuthUser = Depends(require_admin)):
    try:
        execute(
            """
            UPDATE departamento_academico SET facultad=%s, carrera=%s, codigo=%s, activo=%s
            WHERE departamento_academico_id=%s
            """,
            (data.facultad, data.carrera, data.codigo, int(data.activo), item_id),
        )
        return fetch_one("SELECT * FROM departamento_academico WHERE departamento_academico_id = %s", (item_id,))
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.delete("/departamentos-academicos/{item_id}")
def desactivar_departamento(item_id: str, _: AuthUser = Depends(require_admin)):
    execute("UPDATE departamento_academico SET activo = 0 WHERE departamento_academico_id = %s", (item_id,))
    return {"ok": True, "message": "Departamento desactivado."}


@router.get("/limites-rol-tipo")
def listar_limites_rol_tipo(_: AuthUser = Depends(get_current_user)):
    return fetch_all(
        """
        SELECT
            l.limite_rol_id AS id,
            l.limite_rol_id,
            l.tipo_proyecto_id,
            tp.nombre AS tipo_proyecto_nombre,
            l.tipo_liderazgo,
            l.rol_id,
            cr.nombre AS rol_nombre,
            l.cantidad_maxima
        FROM limite_rol_tipo l
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id = l.tipo_proyecto_id
        INNER JOIN catalogo_rol cr ON cr.rol_id = l.rol_id
        ORDER BY tp.nombre, l.tipo_liderazgo, cr.nombre
        """
    )


@router.post("/limites-rol-tipo")
def crear_limite_rol_tipo(data: LimiteRolTipoIn, _: AuthUser = Depends(require_admin)):
    item_id = str(uuid4())
    try:
        execute(
            """
            INSERT INTO limite_rol_tipo
            (limite_rol_id, tipo_proyecto_id, tipo_liderazgo, rol_id, cantidad_maxima)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (item_id, data.tipo_proyecto_id, data.tipo_liderazgo, data.rol_id, data.cantidad_maxima),
        )
        return fetch_one(
            """
            SELECT
                l.limite_rol_id AS id,
                l.limite_rol_id,
                l.tipo_proyecto_id,
                tp.nombre AS tipo_proyecto_nombre,
                l.tipo_liderazgo,
                l.rol_id,
                cr.nombre AS rol_nombre,
                l.cantidad_maxima
            FROM limite_rol_tipo l
            INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id = l.tipo_proyecto_id
            INNER JOIN catalogo_rol cr ON cr.rol_id = l.rol_id
            WHERE l.limite_rol_id = %s
            """,
            (item_id,),
        )
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.put("/limites-rol-tipo/{item_id}")
def actualizar_limite_rol_tipo(item_id: str, data: LimiteRolTipoIn, _: AuthUser = Depends(require_admin)):
    try:
        execute(
            """
            UPDATE limite_rol_tipo
            SET tipo_proyecto_id=%s, tipo_liderazgo=%s, rol_id=%s, cantidad_maxima=%s
            WHERE limite_rol_id=%s
            """,
            (data.tipo_proyecto_id, data.tipo_liderazgo, data.rol_id, data.cantidad_maxima, item_id),
        )
        return fetch_one(
            """
            SELECT
                l.limite_rol_id AS id,
                l.limite_rol_id,
                l.tipo_proyecto_id,
                tp.nombre AS tipo_proyecto_nombre,
                l.tipo_liderazgo,
                l.rol_id,
                cr.nombre AS rol_nombre,
                l.cantidad_maxima
            FROM limite_rol_tipo l
            INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id = l.tipo_proyecto_id
            INNER JOIN catalogo_rol cr ON cr.rol_id = l.rol_id
            WHERE l.limite_rol_id = %s
            """,
            (item_id,),
        )
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.delete("/limites-rol-tipo/{item_id}")
def eliminar_limite_rol_tipo(item_id: str, _: AuthUser = Depends(require_admin)):
    try:
        execute("DELETE FROM limite_rol_tipo WHERE limite_rol_id = %s", (item_id,))
        return {"ok": True, "message": "Límite eliminado."}
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.get("/periodos-academicos")
def listar_periodos(_: AuthUser = Depends(get_current_user)):
    return fetch_all("SELECT * FROM periodo_academico ORDER BY fecha_inicio DESC")


@router.post("/periodos-academicos")
def crear_periodo(data: PeriodoAcademicoIn, _: AuthUser = Depends(require_admin)):
    item_id = str(uuid4())
    try:
        execute(
            """
            INSERT INTO periodo_academico (periodo_academico_id, nombre, fecha_inicio, fecha_fin, activo)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (item_id, data.nombre, data.fecha_inicio, data.fecha_fin, int(data.activo)),
        )
        return fetch_one("SELECT * FROM periodo_academico WHERE periodo_academico_id = %s", (item_id,))
    except DatabaseError as exc:
        _handle_db_error(exc)


@router.get("/convocatorias")
def listar_convocatorias(tipo_proyecto_id: str | None = None, _: AuthUser = Depends(get_current_user)):
    params = []
    where = ""
    if tipo_proyecto_id:
        where = "WHERE c.tipo_proyecto_id = %s"
        params.append(tipo_proyecto_id)
    return fetch_all(
        f"""
        SELECT c.*, tp.nombre AS tipo_proyecto, pa.nombre AS periodo_academico, ce.nombre AS estado
        FROM convocatoria c
        INNER JOIN tipo_proyecto tp ON tp.tipo_proyecto_id = c.tipo_proyecto_id
        INNER JOIN periodo_academico pa ON pa.periodo_academico_id = c.periodo_academico_id
        INNER JOIN catalogo_estado ce ON ce.catalogo_estado_id = c.estado_id
        {where}
        ORDER BY c.fecha_creacion DESC
        """,
        tuple(params),
    )
