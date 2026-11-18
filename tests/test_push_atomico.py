"""El push reemplaza el remoto de golpe, o no lo toca.

**Este archivo existe porque `push_to_turso` no tenía NI UN test.** La única
mención en la suite lo reemplazaba por un lambda, así que las 98 pruebas podían
estar en verde con el push roto, y de hecho lo estuvieron: al cambiarlo se
descubrió que el fake de las otras pruebas no implementa `transaction()`, y nada
reprobó.

Lo que se prueba es la propiedad, no la implementación: después de un push que
falla, el remoto tiene que estar **exactamente** como antes; después de uno que
termina, tiene que tener todo lo nuevo y nada de lo viejo.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from sat69 import database as db
from sat69 import turso
from sat69.config import settings


class _Res:
    def __init__(self, rows):
        self.rows = rows


class _ClienteConTransaccion:
    """Imita libsql con `transaction()`. `revienta_en` corta donde se le diga."""

    def __init__(self, conn: sqlite3.Connection, revienta_en: str | None = None,
                 revienta_en_swap: str | None = None):
        self._conn = conn
        self._revienta_en = revienta_en
        self._revienta_en_swap = revienta_en_swap
        self.swap_intentado = False
        self.swap_commiteado = False

    def execute(self, sql, args=None):
        if self._revienta_en and self._revienta_en in sql:
            raise RuntimeError("Turso se cayó a medio push")
        return _Res(self._conn.execute(sql, args or []).fetchall())

    def batch(self, stmts):
        es_swap = any("RENAME TO" in s.sql for s in stmts)
        if es_swap:
            self.swap_intentado = True
            if self._revienta_en_swap:
                # Un batch es una unidad: si una sentencia falla, NINGUNA queda.
                # Eso es lo que el servidor garantiza y lo que este fake modela.
                raise RuntimeError("el swap se cayó a la mitad")
        for s in stmts:
            if self._revienta_en and self._revienta_en in s.sql:
                raise RuntimeError("Turso se cayó a medio lote")
            self._conn.execute(s.sql, s.args or [])
        if es_swap:
            self.swap_commiteado = True

    def transaction(self):  # pragma: no cover - ya no se usa; ver _normalize_url
        raise RuntimeError("TRANSACTIONS_NOT_SUPPORTED")

    def close(self):
        pass


@pytest.fixture()
def local(tmp_path: Path):
    db.configure(None)
    db.init_db(tmp_path / "local.db")
    yield


@pytest.fixture()
def remoto(monkeypatch):
    conn = sqlite3.connect(":memory:")
    for stmt in turso._SCHEMA_STMTS:
        conn.execute(stmt)
    monkeypatch.setattr(
        turso, "settings", dataclasses.replace(settings, turso_url="libsql://fake")
    )
    yield conn
    conn.close()


def _sembrar(conn, marca: str, filas: int) -> None:
    for i in range(filas):
        conn.execute(
            "INSERT INTO registros_csd (rfc, nombre, supuesto, fecha_cancelacion, "
            "admon_responsable, fecha_publicacion, datos, imported_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"{marca[:3]}{i:06d}XX1", marca, "FRACCION X", "2024-01-01", "AGR",
             "2024-07-23", "[]", "2026-01-01T00:00:00Z"),
        )


def _contar(conn, marca: str | None = None) -> int:
    if marca is None:
        return conn.execute("SELECT count(*) FROM registros_csd").fetchone()[0]
    return conn.execute(
        "SELECT count(*) FROM registros_csd WHERE nombre = ?", (marca,)
    ).fetchone()[0]


def test_un_push_completo_reemplaza_todo(local, remoto, monkeypatch):
    with db.get_conn() as conn:
        _sembrar(conn, "NUEVO", 7)
    _sembrar(remoto, "VIEJO", 4)
    remoto.commit()
    cliente = _ClienteConTransaccion(remoto)
    monkeypatch.setattr(turso, "_client", lambda: cliente)

    assert turso.push_to_turso() == 7
    assert cliente.swap_commiteado, "el swap no llegó a aplicarse"
    assert _contar(remoto, "NUEVO") == 7
    assert _contar(remoto, "VIEJO") == 0, "quedaron filas viejas tras el swap"


def test_un_push_que_truena_antes_del_swap_no_toca_el_remoto(local, remoto, monkeypatch):
    """**El defecto que esto arregla.**

    Antes el push hacía `DELETE FROM <tabla>` sobre la tabla que el servicio lee
    y luego insertaba por lotes, así que una caída a la mitad dejaba el almacén
    durable incompleto hasta la corrida siguiente. Ahora los `INSERT` van a
    tablas de paso y lo único que toca las reales es el swap.
    """
    with db.get_conn() as conn:
        _sembrar(conn, "NUEVO", 7)
    _sembrar(remoto, "VIEJO", 4)
    remoto.commit()
    cliente = _ClienteConTransaccion(remoto, revienta_en="INSERT INTO registros_csd__nuevo")
    monkeypatch.setattr(turso, "_client", lambda: cliente)

    assert turso.push_to_turso() == 0, "un push fallido debe devolver 0"
    assert _contar(remoto) == 4, (
        f"el remoto quedó con {_contar(remoto)} filas: el push lo dejó a medias"
    )
    assert _contar(remoto, "VIEJO") == 4, "las 4 filas de antes ya no son las de antes"


def test_si_el_swap_truena_se_hace_rollback_y_el_remoto_queda_igual(local, remoto, monkeypatch):
    """El swap son diez sentencias; si una falla, las otras nueve no pueden quedar."""
    with db.get_conn() as conn:
        _sembrar(conn, "NUEVO", 7)
    _sembrar(remoto, "VIEJO", 4)
    remoto.commit()
    cliente = _ClienteConTransaccion(remoto, revienta_en_swap="sí")
    monkeypatch.setattr(turso, "_client", lambda: cliente)

    assert turso.push_to_turso() == 0
    assert cliente.swap_intentado, "nunca se intentó el swap"
    assert not cliente.swap_commiteado
    assert _contar(remoto, "VIEJO") == 4, "el batch del swap no protegió al remoto"


def test_las_tablas_de_paso_de_una_corrida_caida_no_estorban(local, remoto, monkeypatch):
    """Una corrida que murió antes del swap deja sus `__nuevo` con datos viejos.
    La siguiente tiene que tirarlas, no insertar encima: si no, el push mandaría
    las filas de las dos corridas juntas."""
    remoto.execute(turso._crear("registros_csd", como="registros_csd__nuevo"))
    remoto.execute(
        "INSERT INTO registros_csd__nuevo (rfc, nombre, supuesto, fecha_cancelacion, "
        "admon_responsable, fecha_publicacion, datos, imported_at) "
        "VALUES ('BAS000001XX1','BASURA DE AYER','FRACCION X','2024-01-01','AGR',"
        "'2024-07-23','[]','2026-01-01T00:00:00Z')"
    )
    remoto.commit()
    with db.get_conn() as conn:
        _sembrar(conn, "NUEVO", 3)
    cliente = _ClienteConTransaccion(remoto)
    monkeypatch.setattr(turso, "_client", lambda: cliente)

    assert turso.push_to_turso() == 3
    assert _contar(remoto, "BASURA DE AYER") == 0, "se colaron las filas de la corrida caída"
    assert _contar(remoto) == 3


def test_el_esquema_de_las_de_paso_es_el_mismo_que_el_de_las_reales():
    """Si se separaran, el swap metería una tabla con otras columnas. Se derivan
    de `_COLUMNAS_DDL`, así que esto es un candado sobre esa derivación."""
    for tabla in turso._TABLES:
        real = turso._crear(tabla)
        paso = turso._crear(tabla, como=f"{tabla}{turso._SUFIJO_PASO}")
        assert real.replace(tabla, "T", 1) == paso.replace(
            f"{tabla}{turso._SUFIJO_PASO}", "T", 1
        ), f"el esquema de paso de {tabla} no coincide con el real"
    # Y el UNIQUE de source_files tiene que estar en los dos.
    assert "UNIQUE" in turso._crear("source_files")
    assert "UNIQUE" in turso._crear("source_files", como="source_files__nuevo")


# --- El transporte: HTTP, y por qué no WebSocket --------------------------

@pytest.mark.parametrize(("entra", "sale"), [
    ("libsql://sat69.turso.io", "https://sat69.turso.io"),
    ("wss://sat69.turso.io", "https://sat69.turso.io"),
    ("ws://localhost:8080", "https://localhost:8080"),
    ("https://sat69.turso.io", "https://sat69.turso.io"),
    ("file:///tmp/sat69.db", "file:///tmp/sat69.db"),   # las pruebas
])
def test_la_url_se_fuerza_a_http(entra, sale):
    """**El candado de los dos experimentos del 2026-11-18, en ese orden.**

    1. Con `https://`, el push atómico murió con `TRANSACTIONS_NOT_SUPPORTED:
       The HTTP client does not support transactions. Please use a libsql:, ws:
       or wss: URL`.
    2. Siguiendo ese consejo se cambió a `wss://`, y el pull murió en el primer
       intento con `400, message='Invalid response status'`.

    O sea que el consejo del mensaje de error NO aplica a este endpoint, y la
    normalización a `https://` del commit inicial era necesaria: le faltaba el
    comentario, no el motivo. Por eso el swap va en un `batch` y no en una
    transacción.

    Si alguien vuelve a poner `wss://` aquí porque el mensaje de Turso se lo
    pide, este test truena antes de que lo descubra un cron.
    """
    assert turso._normalize_url(entra) == sale


def test_el_swap_no_usa_transaction():
    """La otra mitad del mismo hecho: sobre HTTP no hay transacciones, así que
    `push_to_turso` no puede llamar a `transaction()`.

    Se revisa con `ast` y no grepeando el texto, y eso me lo enseñó este mismo
    test: la primera versión buscaba la cadena en el fuente y reprobó porque
    `client.transaction()` aparece en el DOCSTRING, explicando por qué no se
    usa. Un grep sobre código no distingue una llamada de una mención.
    """
    import ast
    import inspect

    arbol = ast.parse(inspect.getsource(turso.push_to_turso))
    llamadas = {
        nodo.func.attr
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)
    }
    assert "transaction" not in llamadas, (
        "el push volvió a llamar a transaction(): sobre el transporte HTTP de "
        "este endpoint eso falla con TRANSACTIONS_NOT_SUPPORTED"
    )
    assert "batch" in llamadas, "el swap tiene que ir en un batch"
