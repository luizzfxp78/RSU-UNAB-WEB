from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import mysql.connector
from mysql.connector import MySQLConnection

from app.core.config import get_settings


class DatabaseError(RuntimeError):
    pass


@contextmanager
def get_connection() -> Iterable[MySQLConnection]:
    settings = get_settings()
    tls_options = ({"ssl_ca": settings.db_ssl_ca, "ssl_verify_cert": True, "ssl_verify_identity": True}
                   if settings.db_ssl_ca else {})
    conn = mysql.connector.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
        use_unicode=True,
        autocommit=False,
        **tls_options,
    )
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(query: str, params: Sequence[Any] | None = None) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, tuple(params or ()))
        rows = cursor.fetchall()
        cursor.close()
        return rows


def fetch_one(query: str, params: Sequence[Any] | None = None) -> Optional[Dict[str, Any]]:
    """Devuelve la primera fila sin dejar resultados pendientes en MySQL.

    mysql-connector usa cursores no almacenados en búfer de forma predeterminada.
    Si una consulta que conceptualmente se usa como existencia devuelve varias
    filas (por ejemplo, information_schema.statistics para un índice compuesto),
    cerrar el cursor después de ``fetchone()`` produce ``Unread result found``.
    El cursor almacenado en búfer consume el conjunto completo antes de cerrarse.
    """
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=True, buffered=True)
        try:
            cursor.execute(query, tuple(params or ()))
            return cursor.fetchone()
        finally:
            cursor.close()


def execute(query: str, params: Sequence[Any] | None = None) -> int:
    with get_connection() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params or ()))
            affected = cursor.rowcount
            conn.commit()
            cursor.close()
            return affected
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise DatabaseError(str(exc)) from exc


def execute_many(query: str, values: Sequence[Sequence[Any]]) -> int:
    with get_connection() as conn:
        try:
            cursor = conn.cursor()
            cursor.executemany(query, values)
            affected = cursor.rowcount
            conn.commit()
            cursor.close()
            return affected
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise DatabaseError(str(exc)) from exc
