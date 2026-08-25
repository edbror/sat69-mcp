"""
Sincronización Turso (libSQL cloud) ↔ SQLite local.

Estrategia (igual que el MCP de movilizaciones):
  • El SQLite local (efímero en Render) atiende todas las consultas + FTS5.
  • Turso es el almacén durable que sobrevive a los redeploys.
  • Al arrancar → pull_from_turso(): Turso → local (los triggers reconstruyen el FTS).
  • Tras importar → push_to_turso(): local → Turso.

Supuesto: un único escritor (la instancia de Render / el cron). Configurar
TURSO_DATABASE_URL + TURSO_AUTH_TOKEN habilita el sync; si faltan, todo es no-op.
"""
from __future__ import annotations

import logging

from .config import settings

logger = logging.getLogger(__name__)

_TABLES = {
    "registros_69": [
        "id", "rfc", "razon_social", "tipo_persona", "supuesto",
        "fecha_primera_publicacion", "entidad_federativa", "source_file", "imported_at",
    ],
    "registros_69b": [
        "id", "rfc", "nombre", "situacion", "oficio_presuncion_sat",
        "publicacion_sat_presuntos", "publicacion_dof_presuntos",
        "publicacion_sat_desvirtuados", "publicacion_dof_desvirtuados",
        "oficio_definitivos_sat", "publicacion_sat_definitivos",
        "publicacion_dof_definitivos", "publicacion_sat_sentencia_favorable",
        "publicacion_dof_sentencia_favorable", "datos", "imported_at",
    ],
    "registros_69b_bis": [
        "id", "rfc", "nombre", "situacion",
        "oficio_definitivo_sat", "publicacion_sat_definitivo",
        "oficio_definitivo_dof", "publicacion_dof_definitivo",
        "oficio_sentencia_favorable_sat", "publicacion_sat_sentencia_favorable",
        "oficio_sentencia_favorable_dof", "publicacion_dof_sentencia_favorable",
        "datos", "imported_at",
    ],
    "source_files": [
        "id", "dataset", "source_file", "sha256", "rows",
        "sat_actualizado_al", "status", "error_msg", "fetched_at",
    ],
}

_SCHEMA_STMTS = [
    """CREATE TABLE IF NOT EXISTS registros_69 (
        id INTEGER PRIMARY KEY, rfc TEXT, razon_social TEXT, tipo_persona TEXT,
        supuesto TEXT, fecha_primera_publicacion TEXT, entidad_federativa TEXT,
        source_file TEXT, imported_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS registros_69b (
        id INTEGER PRIMARY KEY, rfc TEXT, nombre TEXT, situacion TEXT,
        oficio_presuncion_sat TEXT, publicacion_sat_presuntos TEXT,
        publicacion_dof_presuntos TEXT, publicacion_sat_desvirtuados TEXT,
        publicacion_dof_desvirtuados TEXT, oficio_definitivos_sat TEXT,
        publicacion_sat_definitivos TEXT, publicacion_dof_definitivos TEXT,
        publicacion_sat_sentencia_favorable TEXT,
        publicacion_dof_sentencia_favorable TEXT, datos TEXT, imported_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS registros_69b_bis (
        id INTEGER PRIMARY KEY, rfc TEXT, nombre TEXT, situacion TEXT,
        oficio_definitivo_sat TEXT, publicacion_sat_definitivo TEXT,
        oficio_definitivo_dof TEXT, publicacion_dof_definitivo TEXT,
        oficio_sentencia_favorable_sat TEXT, publicacion_sat_sentencia_favorable TEXT,
        oficio_sentencia_favorable_dof TEXT, publicacion_dof_sentencia_favorable TEXT,
        datos TEXT, imported_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS source_files (
        id INTEGER PRIMARY KEY, dataset TEXT, source_file TEXT UNIQUE, sha256 TEXT,
        rows INTEGER, sat_actualizado_al TEXT, status TEXT, error_msg TEXT,
        fetched_at TEXT)""",
]

_PUSH_CHUNK = 2000
_PULL_CHUNK = 5000  # ponytail: pull por lotes; 512Mi de Render no aguanta ½M dicts de golpe


def _normalize_url(url: str) -> str:
    for prefix in ("libsql://", "wss://"):
        if url.startswith(prefix):
            return "https://" + url[len(prefix):]
    return url


def _client():
    if not settings.turso_enabled:
        return None
    try:
        import libsql_client
        return libsql_client.create_client_sync(
            url=_normalize_url(settings.turso_url), auth_token=settings.turso_token
        )
    except ImportError:
        logger.error("libsql-client no instalado — pip install libsql-client")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("No se pudo conectar a Turso: %s", exc)
        return None


def pull_from_turso() -> int:
    """Turso → local (reemplazo completo, por lotes). Devuelve filas importadas.

    Lee cada tabla en páginas de _PULL_CHUNK e inserta sobre la marcha, para no
    materializar ½M de filas en memoria (el free tier de Render son 512Mi).
    """
    client = _client()
    if client is None:
        logger.info("Turso no configurado — se omite pull")
        return 0

    from . import database as db

    total = 0
    try:
        for stmt in _SCHEMA_STMTS:
            client.execute(stmt)

        for table, cols in _TABLES.items():
            n = client.execute(f"SELECT count(*) FROM {table}").rows[0][0]
            with db.get_conn() as conn:
                conn.execute(f"DELETE FROM {table}")
            if not n:
                continue
            col_list = ", ".join(cols)
            insert = f"INSERT INTO {table} ({col_list}) VALUES ({', '.join('?' for _ in cols)})"
            for offset in range(0, n, _PULL_CHUNK):
                res = client.execute(
                    f"SELECT {col_list} FROM {table} LIMIT {_PULL_CHUNK} OFFSET {offset}"
                )
                with db.get_conn() as conn:
                    conn.executemany(insert, [list(row) for row in res.rows])
                total += len(res.rows)
    except Exception as exc:  # noqa: BLE001
        logger.error("pull_from_turso falló: %s", exc)
        return total
    finally:
        client.close()

    if total == 0:
        logger.info("Turso vacío — nada que importar")
    else:
        logger.info("Importadas %d filas de Turso", total)
    return total


def push_to_turso() -> int:
    """Local → Turso (reemplazo completo, en lotes). Devuelve total enviado."""
    client = _client()
    if client is None:
        logger.info("Turso no configurado — se omite push")
        return 0

    from libsql_client import Statement

    from . import database as db
    with db.get_conn() as conn:
        data = {
            t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()]
            for t in _TABLES
        }

    try:
        for stmt in _SCHEMA_STMTS:
            client.execute(stmt)

        total = 0
        for table, cols in _TABLES.items():
            client.execute(f"DELETE FROM {table}")
            rows = data[table]
            placeholders = ", ".join("?" for _ in cols)
            insert = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
            for i in range(0, len(rows), _PUSH_CHUNK):
                batch = [
                    Statement(insert, [r.get(c) for c in cols])
                    for r in rows[i:i + _PUSH_CHUNK]
                ]
                if batch:
                    client.batch(batch)
                    total += len(batch)
        logger.info("Enviadas %d filas a Turso", total)
        return total
    except Exception as exc:  # noqa: BLE001
        logger.error("push_to_turso falló: %s", exc)
        return 0
    finally:
        client.close()
