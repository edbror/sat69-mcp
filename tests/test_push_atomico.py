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


class _TxFalsa:
    """Una transacción contra el SQLite en memoria que hace de Turso.

    No usa `BEGIN`/`COMMIT` de sqlite3 porque el remoto falso es una conexión en
    memoria con autocommit implícito; para lo que importa aquí basta con
    acumular las sentencias y aplicarlas sólo en el `commit`. Eso modela la
    propiedad que el cliente real sí da (comprobada contra libsql antes de
    escribir esto) y deja ver si el código llama a `rollback` donde debe.
    """

    def __init__(self, conn: sqlite3.Connection, revienta_en: str | None = None):
        self._conn = conn
        self._revienta_en = revienta_en
        self._pendientes: list[str] = []
        self.commiteada = False
        self.rollbackeada = False

    def execute(self, sql, args=None):
        if self._revienta_en and self._revienta_en in sql:
            raise RuntimeError("el swap se cayó a la mitad")
        self._pendientes.append(sql)
        return _Res([])

    def commit(self):
        for sql in self._pendientes:
            self._conn.execute(sql)
        self.commiteada = True

    def rollback(self):
        self._pendientes.clear()
        self.rollbackeada = True


class _ClienteConTransaccion:
    """Imita libsql con `transaction()`. `revienta_en` corta donde se le diga."""

    def __init__(self, conn: sqlite3.Connection, revienta_en: str | None = None,
                 revienta_en_swap: str | None = None):
        self._conn = conn
        self._revienta_en = revienta_en
        self._revienta_en_swap = revienta_en_swap
        self.tx: _TxFalsa | None = None

    def execute(self, sql, args=None):
        if self._revienta_en and self._revienta_en in sql:
            raise RuntimeError("Turso se cayó a medio push")
        return _Res(self._conn.execute(sql, args or []).fetchall())

    def batch(self, stmts):
        for s in stmts:
            if self._revienta_en and self._revienta_en in s.sql:
                raise RuntimeError("Turso se cayó a medio lote")
            self._conn.execute(s.sql, s.args)

    def transaction(self):
        self.tx = _TxFalsa(self._conn, revienta_en=self._revienta_en_swap)
        return self.tx

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
    assert cliente.tx is not None and cliente.tx.commiteada
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
    cliente = _ClienteConTransaccion(remoto, revienta_en_swap="RENAME TO source_files")
    monkeypatch.setattr(turso, "_client", lambda: cliente)

    assert turso.push_to_turso() == 0
    assert cliente.tx is not None
    assert cliente.tx.rollbackeada, "no se llamó a rollback tras fallar el swap"
    assert not cliente.tx.commiteada
    assert _contar(remoto, "VIEJO") == 4, "el rollback no protegió al remoto"


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


# --- El transporte: WebSocket, no HTTP ---------------------------------------

@pytest.mark.parametrize(("entra", "sale"), [
    ("libsql://sat69.turso.io", "wss://sat69.turso.io"),
    ("https://sat69.turso.io", "wss://sat69.turso.io"),
    ("wss://sat69.turso.io", "wss://sat69.turso.io"),
    ("http://localhost:8080", "ws://localhost:8080"),      # sqld local sin TLS
    ("file:///tmp/sat69.db", "file:///tmp/sat69.db"),      # las pruebas
])
def test_la_url_se_fuerza_a_websocket(entra, sale):
    """**El candado del incidente del 2026-11-18.**

    `_normalize_url` hacía lo contrario: forzaba `https://`. El transporte HTTP
    de libsql NO soporta transacciones, así que el swap atómico del push moría
    con `TRANSACTIONS_NOT_SUPPORTED` y el remoto se quedaba sin la ingesta del
    día. Si alguien vuelve a poner `https://` aquí, este test truena antes de
    que lo descubra un cron.
    """
    assert turso._normalize_url(entra) == sale


def test_ninguna_url_de_turso_acaba_en_http():
    """Dicho como propiedad y no caso por caso, para que una forma nueva de URL
    no se escape: nada que entre puede salir hablando HTTP."""
    for entra in ("libsql://a.b", "https://a.b", "wss://a.b", "http://a.b:8080"):
        salida = turso._normalize_url(entra)
        assert not salida.startswith(("http://", "https://")), (
            f"{entra} salió como {salida}: el transporte HTTP no tiene transacciones"
        )
