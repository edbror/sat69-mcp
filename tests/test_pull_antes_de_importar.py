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
    monkeypatch.setattr(turso, "push_to_turso", lambda: pushes.append(1) or 0)

    primera = pipeline.process_import("csd")
    assert primera["rows_importados"] == 3
    assert len(pushes) == 1, "la primera sí tiene que pushear"

    segunda = pipeline.process_import("csd")
    assert all(r.get("cached") for r in segunda["resultados"]), segunda
    assert len(pushes) == 1, "se pusheó de nuevo sin que nada cambiara"
    assert segunda["turso_rows_pushed"] == 0
