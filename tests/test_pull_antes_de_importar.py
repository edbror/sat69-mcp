"""El cron importaba sin estado previo, y eso dejaba sin piso a dos cosas.

`refresh.yml` arranca con un `/tmp/sat69.db` vacío cada corrida. Con eso,
`_verificar_frescura` tomaba siempre el camino de "primera importación: no hay
nada que proteger" —o sea que sus tres guards no corrían NUNCA en producción— y
la caché por hash no podía acertar, así que cada corrida reescribía 580k filas a
Turso aunque el SAT no hubiera publicado nada.

Estas pruebas cubren el pull que arregla eso y, sobre todo, las dos formas en
que ese pull puede hacer daño si se hace mal.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import httpx
import pytest

from sat69 import database as db
from sat69 import fetcher, pipeline, turso
from sat69.config import settings

_FIXTURE_CSD = Path(__file__).parent / "fixtures" / "csd_sample.csv"


class _Res:
    def __init__(self, rows):
        self.rows = rows


class _FakeClient:
    """Imita el client de libsql. `truena_en` corta el pull a media tabla."""

    def __init__(self, conn: sqlite3.Connection, truena_en: str | None = None):
        self._conn = conn
        self._truena_en = truena_en

    def execute(self, sql, args=None):
        if self._truena_en and self._truena_en in sql:
            raise RuntimeError("Turso se cayó a medio pull")
        return _Res(self._conn.execute(sql, args or []).fetchall())

    def batch(self, stmts):
        for s in stmts:
            self._conn.execute(s.sql, s.args)

    def close(self):
        pass


@pytest.fixture()
def local(tmp_path: Path):
    """La base local del runner: vacía, como el /tmp/sat69.db de cada corrida."""
    db.configure(None)
    db.init_db(tmp_path / "local.db")
    yield


@pytest.fixture()
def remoto(monkeypatch):
    """Un Turso falso en memoria, con el esquema ya puesto."""
    conn = sqlite3.connect(":memory:")
    for stmt in turso._SCHEMA_STMTS:
        conn.execute(stmt)
    monkeypatch.setattr(turso, "_client", lambda: _FakeClient(conn))
    monkeypatch.setattr(
        turso, "settings", dataclasses.replace(settings, turso_url="libsql://fake")
    )
    monkeypatch.setattr(
        pipeline, "settings", dataclasses.replace(settings, turso_url="libsql://fake")
    )
    yield conn
    conn.close()


def _sembrar_remoto(conn, filas_csd: int, declaradas: int | None = None) -> None:
    """Deja en el "Turso" unas filas de CSD y su renglón en source_files."""
    for i in range(filas_csd):
        conn.execute(
            "INSERT INTO registros_csd (rfc, nombre, supuesto, fecha_cancelacion, "
            "admon_responsable, fecha_publicacion, datos, imported_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (f"AAA{i:06d}XX1", "EMPRESA", "FRACCION X", "2024-01-01", "AGR",
             "2024-07-23", "[]", "2026-10-22T00:00:00Z"),
        )
    conn.execute(
        "INSERT INTO source_files (dataset, source_file, sha256, rows, "
        "sat_actualizado_al, status, error_msg, fetched_at) VALUES (?,?,?,?,?,?,?,?)",
        ("csd", "CSDsinefectos.csv", "hash-remoto",
         declaradas if declaradas is not None else filas_csd,
         "2026-10-19", "ok", None, "2026-10-22T00:00:00Z"),
    )


def _sembrar_las_otras_tres(conn) -> None:
    """Una fila en cada una de las otras tres listas, para que el índice traído se
    vea completo. `listas_incompletas` marca cualquier tabla en cero."""
    conn.execute(
        "INSERT INTO registros_69 (rfc, razon_social, tipo_persona, supuesto, "
        "fecha_primera_publicacion, entidad_federativa, source_file, imported_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("FIR010101AA1", "X", "M", "FIRMES", "2020-01-01", "JALISCO", "Firmes.csv", "t"),
    )
    conn.execute(
        "INSERT INTO registros_69b (rfc, nombre, situacion, datos, imported_at) "
        "VALUES (?,?,?,?,?)", ("EFO010101AA1", "X", "Definitivo", "[]", "t")
    )
    conn.execute(
        "INSERT INTO registros_69b_bis (rfc, nombre, situacion, datos, imported_at) "
        "VALUES (?,?,?,?,?)", ("BIS010101AA1", "X", "Definitivo", "[]", "t")
    )
    for ds, src in (("69", "Firmes.csv"), ("69b", "L.csv"), ("69bbis", "B.csv")):
        conn.execute(
            "INSERT INTO source_files (dataset, source_file, sha256, rows, status) "
            "VALUES (?,?,?,?,?)", (ds, src, "h", 1, "ok")
        )


# --- El pull ya no se traga la falla -----------------------------------------

def test_un_pull_que_truena_a_medias_levanta_en_vez_de_devolver_el_parcial(
    local, remoto, monkeypatch
):
    """Devolver el total parcial hacía que una falla fuera indistinguible de un pull
    chico y exitoso. Es exactamente lo que pasó el 18-oct: el log dijo "Importadas
    15,024 filas" y el servicio sirvió el 2.7% de la lista todo el día."""
    _sembrar_remoto(remoto, 5)
    monkeypatch.setattr(turso, "_client", lambda: _FakeClient(remoto, truena_en="registros_csd"))

    with pytest.raises(turso.PullFallido, match="pull incompleto"):
        turso.pull_from_turso()


def test_un_pull_completo_devuelve_las_filas(local, remoto):
    _sembrar_remoto(remoto, 5)
    assert turso.pull_from_turso() == 6  # 5 de CSD + 1 de source_files


# --- Las dos formas en que el pull puede hacer daño --------------------------

def test_si_el_pull_truena_no_se_importa_nada(local, remoto, monkeypatch, caplog):
    """Importar sobre una base vacía y pushear reemplazaría el remoto con una
    ingesta que ningún guard revisó. Mejor no correr que correr a ciegas."""
    _sembrar_remoto(remoto, 5)
    monkeypatch.setattr(turso, "_client", lambda: _FakeClient(remoto, truena_en="registros_csd"))

    llamadas = []
    monkeypatch.setattr(
        pipeline, "process_import", lambda *a, **k: llamadas.append((a, k)) or {"success": True}
    )
    monkeypatch.setattr(pipeline.sys if hasattr(pipeline, "sys") else pipeline, "argv", [], False)

    assert pipeline.main() == 1
    assert llamadas == [], "se importó a pesar de que el pull falló"


def test_un_pull_a_medias_fuerza_la_ingesta_completa(local, remoto):
    """Con un pull parcial la caché por hash saltaría archivos cuyas filas locales
    están incompletas, y el push de después mandaría ese pedazo a Turso. O sea que
    la caché sólo se puede creer si el piso está completo."""
    # El remoto declara 59,636 filas de CSD y sólo tiene 5: eso llega al local. Las
    # otras tres van completas, para que el único defecto sea el que se prueba.
    _sembrar_remoto(remoto, 5, declaradas=59636)
    _sembrar_las_otras_tres(remoto)

    assert pipeline._traer_estado_previo() is True
    assert db.listas_incompletas(), "el índice traído tenía que verse a medias"


def test_un_pull_completo_deja_creerle_a_la_cache(local, remoto):
    """Las cuatro listas con datos, que es como se ve producción. Una lista en cero
    cuenta como índice a medias y fuerza, y está bien: ninguna de las cuatro está
    vacía nunca en la vida real."""
    _sembrar_remoto(remoto, 5, declaradas=5)
    _sembrar_las_otras_tres(remoto)

    assert pipeline._traer_estado_previo() is False
    assert db.listas_incompletas() == []
    assert db.get_source_hash("CSDsinefectos.csv") == "hash-remoto", (
        "sin el hash previo la idempotencia no puede acertar"
    )


def test_sin_turso_configurado_se_importa_todo(local, monkeypatch):
    monkeypatch.setattr(pipeline, "settings", dataclasses.replace(settings, turso_url=""))
    assert pipeline._traer_estado_previo() is True


def test_un_turso_vacio_se_importa_todo(local, remoto):
    assert pipeline._traer_estado_previo() is True


# --- El push deja de reescribir lo idéntico ---------------------------------

def _stub_descarga(monkeypatch, cuerpo: bytes, cuando: str = "Mon, 19 Oct 2026 23:55:46 GMT"):
    class _Resp:
        headers = {"last-modified": cuando}
        content = cuerpo

        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx.Client, "get", lambda self, url: _Resp())
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: None)


def test_si_nada_cambio_no_se_pushea(local, monkeypatch):
    """Con el pull puesto, una corrida donde todo salió de la caché deja el local
    igual al remoto. Pushear 580k filas para reescribirlas idénticas es gasto, y
    una ventana más de pull parcial para quien arranque en ese momento."""
    _stub_descarga(monkeypatch, _FIXTURE_CSD.read_bytes())
    pushes = []

    # El stub devuelve un conteo y no 0, y eso ya no es un detalle: desde que un
    # push que devuelve 0 habiendo cambios se cuenta como FALLA, un stub que
    # devolvía 0 modelaba un push roto mientras el test decía "la primera sí
    # tiene que pushear".
    def _push_ok():
        pushes.append(1)
        return 4  # 3 filas de CSD + su renglón de source_files

    monkeypatch.setattr(turso, "push_to_turso", _push_ok)

    primera = pipeline.process_import("csd")
    assert primera["rows_importados"] == 3
    assert len(pushes) == 1, "la primera sí tiene que pushear"
    assert primera["success"] is True, primera["errores"]

    segunda = pipeline.process_import("csd")
    assert all(r.get("cached") for r in segunda["resultados"]), segunda
    assert len(pushes) == 1, "se pusheó de nuevo sin que nada cambiara"
    assert segunda["turso_rows_pushed"] == 0


# --- El pull es atómico: una transacción, no un commit por lote --------------
#
# Medido el 2026-11-16 contra una base sembrada con los volúmenes reales
# (508,264 filas del 69 y 60,582 del CSD, 168 MB), comparando el pull con commit
# por lote contra el pull en una transacción:
#
#   lecturas parciales de un lector concurrente : 32 de 44   ->  0 de 106
#   lo que deja una caída en el lote 40         : 39.3% de la lista -> índice
#                                                 anterior intacto
#   duración                                    : 23.1s -> 56.1s
#   WAL máximo                                  : 129 MB -> 177 MB
#
# Los dos tests de abajo son esas dos filas. Con el código viejo los dos
# reprueban, que es la única razón por la que valen.


class _ClienteQueFallaEnLaPagina:
    """Como `_FakeClient`, pero truena en la N-ésima página de `SELECT ... LIMIT`.

    Hace falta porque `truena_en` de `_FakeClient` revienta en el primer
    `execute` que menciona la tabla, y ése es el `count(*)`: eso pasa ANTES de
    cualquier escritura local, así que no ejercita el rollback.
    """

    def __init__(self, conn: sqlite3.Connection, pagina: int):
        self._conn = conn
        self._pagina = pagina
        self._vistas = 0

    def execute(self, sql, args=None):
        if "LIMIT" in sql and "registros_csd" in sql:
            self._vistas += 1
            if self._vistas > self._pagina:
                raise RuntimeError("Turso se cayó a media página")
        return _Res(self._conn.execute(sql, args or []).fetchall())

    def batch(self, stmts):
        for s in stmts:
            self._conn.execute(s.sql, s.args)

    def close(self):
        pass


class _ClienteQueEspia:
    """Imita libsql y, en cada página, anota qué vería un lector concurrente.

    Es el servicio atendiendo consultas mientras el pull corre. Abre su PROPIA
    conexión al mismo archivo, que es lo que hace un lector de verdad.
    """

    def __init__(self, conn: sqlite3.Connection, ruta: Path):
        self._conn = conn
        self._ruta = ruta
        self.visto: list[int] = []

    def _espiar(self) -> None:
        lector = sqlite3.connect(self._ruta, timeout=5)
        try:
            self.visto.append(lector.execute("SELECT count(*) FROM registros_csd").fetchone()[0])
        finally:
            lector.close()

    def execute(self, sql, args=None):
        if "LIMIT" in sql:
            self._espiar()
        return _Res(self._conn.execute(sql, args or []).fetchall())

    def batch(self, stmts):
        for s in stmts:
            self._conn.execute(s.sql, s.args)

    def close(self):
        pass


def _sembrar_local_csd(filas: int) -> None:
    """Un índice anterior en la base local, para tener algo que perder."""
    with db.get_conn() as conn:
        for i in range(filas):
            conn.execute(
                "INSERT INTO registros_csd (rfc, nombre, supuesto, fecha_cancelacion, "
                "admon_responsable, fecha_publicacion, datos, imported_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (f"VIE{i:06d}XX1", "LO QUE YA SERVIA", "FRACCION X", "2024-01-01",
                 "AGR", "2024-07-23", "[]", "2026-01-01T00:00:00Z"),
            )


def test_un_pull_que_truena_deja_el_indice_anterior_intacto(local, remoto, monkeypatch):
    """**El incidente del 18-oct, convertido en test.**

    Ese día el servicio importó 15,024 de 557,094 filas y pasó el día
    contestando con el 2.7% de la lista. Con commit por lote, una caída a media
    tabla deja instalado lo que alcanzó a entrar; con una transacción, el
    rollback devuelve el índice anterior completo.

    Antes esto no se probaba: el test de arriba sólo verifica que se levante
    `PullFallido`, y levantar no dice nada sobre lo que quedó en disco.
    """
    _sembrar_local_csd(12)
    _sembrar_remoto(remoto, 40)
    monkeypatch.setattr(turso, "_PULL_CHUNK", 5)
    monkeypatch.setattr(turso, "_client", lambda: _ClienteQueFallaEnLaPagina(remoto, pagina=3))

    with pytest.raises(turso.PullFallido):
        turso.pull_from_turso()

    with db.get_conn() as conn:
        quedaron = conn.execute("SELECT count(*) FROM registros_csd").fetchone()[0]
        viejas = conn.execute(
            "SELECT count(*) FROM registros_csd WHERE nombre = 'LO QUE YA SERVIA'"
        ).fetchone()[0]
    assert quedaron == 12, (
        f"el pull falló y dejó {quedaron} filas: el índice anterior se perdió. "
        "Con commit por lote quedarían las que alcanzaron a entrar."
    )
    assert viejas == 12, "quedaron 12 filas pero no son las de antes"


def test_nadie_ve_el_indice_a_medias_mientras_el_pull_corre(local, remoto, monkeypatch, tmp_path):
    """**La ventana que se midió en producción el 12-nov**, cuando el servicio
    subió de 165 mil a 508 mil filas con `69b` en cero durante unos dos minutos.

    El espía abre su propia conexión en cada página, que es lo que hace el
    servicio atendiendo una consulta. En WAL un lector ve el último estado
    COMPROMETIDO, así que con una sola transacción tiene que ver siempre el
    índice anterior, nunca uno a medias.
    """
    _sembrar_local_csd(12)
    _sembrar_remoto(remoto, 40)
    _sembrar_las_otras_tres(remoto)
    monkeypatch.setattr(turso, "_PULL_CHUNK", 5)
    espia = _ClienteQueEspia(remoto, Path(db._path()))
    monkeypatch.setattr(turso, "_client", lambda: espia)

    turso.pull_from_turso()

    assert len(espia.visto) >= 4, (
        f"sólo {len(espia.visto)} muestras; sin varias páginas el test no prueba nada"
    )
    parciales = [n for n in espia.visto if n not in (12, 40)]
    assert not parciales, (
        f"un lector vio el índice a medias durante el pull: {espia.visto}. "
        "Con commit por lote se ven los conteos intermedios."
    )
    assert espia.visto[0] == 12, (
        f"la primera muestra debería ver el índice ANTERIOR completo, vio {espia.visto[0]}"
    )
    with db.get_conn() as conn:
        assert conn.execute("SELECT count(*) FROM registros_csd").fetchone()[0] == 40


# --- Un push fallido no se disfraza de día tranquilo -------------------------

def test_un_push_que_no_envia_nada_habiendo_cambios_es_un_error(local, monkeypatch, caplog):
    """**El defecto que el #28 introdujo sin querer, y que este test cierra.**

    `turso_rows_pushed` valía 0 en dos casos muy distintos: cuando no había nada
    que empujar, y cuando el push falló. Desde que el workflow decide con ese
    número, un push roto imprimía "el SAT no publicó nada nuevo" y la corrida
    pasaba en VERDE todos los días, mientras el almacén durable se quedaba sin
    lo que la ingesta acababa de importar y el servicio seguía sirviendo el
    corte viejo.

    Ahora eso cuenta como error, con lo cual `main()` sale con 1 y el paso del
    workflow truena ANTES de llegar al aviso al servicio.
    """
    _stub_descarga(monkeypatch, _FIXTURE_CSD.read_bytes())
    monkeypatch.setattr(turso, "push_to_turso", lambda: 0)  # falló

    res = pipeline.process_import("csd")

    assert res["rows_importados"] == 3, "la importación local sí funcionó"
    assert res["success"] is False, (
        "un push que no envió nada habiendo cambios tiene que marcar la corrida "
        "como fallida; si no, el workflow pasa en verde con el remoto viejo"
    )
    assert any("push a Turso" in e.get("source_file", "") for e in res["errores"]), res["errores"]
    assert any("almacén durable" in r.message for r in caplog.records), (
        "el log tiene que decir qué se perdió, no sólo que algo falló"
    )


def test_cuando_no_hubo_cambios_el_cero_sigue_siendo_normal(local, monkeypatch):
    """El otro lado del mismo candado: sin cambios, 0 es correcto y la corrida
    pasa. Sin este test, hacer que TODO cero fuera error dejaría el cron en rojo
    todos los días que el SAT no publica, que son casi todos."""
    _stub_descarga(monkeypatch, _FIXTURE_CSD.read_bytes())
    llamadas = []
    monkeypatch.setattr(turso, "push_to_turso", lambda: llamadas.append(1) or 9)

    pipeline.process_import("csd")          # primera: importa y pushea
    segunda = pipeline.process_import("csd")  # segunda: todo de caché

    assert segunda["turso_rows_pushed"] == 0
    assert segunda["success"] is True, segunda["errores"]
    assert len(llamadas) == 1, "no debió pushear la segunda vez"
