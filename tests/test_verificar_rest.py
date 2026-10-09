"""Ruta REST /verificar: el mismo veredicto que la tool, sin hablar MCP.

Nace de MATERIA (WE-514), que necesita monitorear proveedores contra el 69-B sin
montar un handshake de sesión MCP por cada RFC.

El test que de verdad importa es el del índice vacío. Con las tablas sin datos,
`verificar_rfc` devuelve LIMPIO para cualquier RFC del mundo — y un consumidor lo
guardaría como "verificado, sin hallazgos". Eso es una constancia falsa en un
expediente de defensa fiscal. Tiene que ser 503.
"""

from __future__ import annotations

import importlib

import pytest
from starlette.testclient import TestClient

KEY = "llave-rest-123"

VEREDICTO = {
    "rfc": "PRO950101AA1",
    "riesgo": "CRITICO",
    "veredicto": "EFOS DEFINITIVO (Art. 69-B).",
    "en_69b": True,
}

ESTADO_OK = {
    "status": "ok",
    "art_69b": {"total_registros": 14523, "sat_actualizado_al": "2026-08-31"},
}


def _app(monkeypatch, *, oauth: bool):
    monkeypatch.setenv("MCP_API_KEY", KEY)
    monkeypatch.setenv("FETCH_ON_STARTUP", "false")
    if oauth:
        monkeypatch.setenv("AUTHKIT_DOMAIN", "https://fake.authkit.app")
        monkeypatch.setenv("BASE_URL", "http://testserver")
    else:
        monkeypatch.delenv("AUTHKIT_DOMAIN", raising=False)

    import sat69.config
    import sat69.server
    import sat69.web

    importlib.reload(sat69.config)
    importlib.reload(sat69.server)
    importlib.reload(sat69.web)
    yield sat69.web.create_app()

    monkeypatch.delenv("MCP_API_KEY")
    monkeypatch.delenv("AUTHKIT_DOMAIN", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)
    importlib.reload(sat69.config)
    importlib.reload(sat69.server)
    importlib.reload(sat69.web)


@pytest.fixture
def app(monkeypatch):
    yield from _app(monkeypatch, oauth=False)


@pytest.fixture
def app_oauth(monkeypatch):
    """Con OAuth ENCENDIDO, que es como corre en producción.

    `/verificar` es un `Route` explícito: no cae en el `Mount` de FastMCP, así que
    si el middleware no le exige el bearer, nadie más lo hace. Y
    `_needs_static_bearer` devuelve `not oauth_enabled` por defecto — con OAuth ON
    la ruta quedaría ABIERTA salvo por su línea explícita. Probarlo con OAuth
    apagado pasa por la razón equivocada.
    """
    yield from _app(monkeypatch, oauth=True)


def _datos(monkeypatch, estado=None, veredicto=None):
    import sat69.database

    monkeypatch.setattr(sat69.database, "estado_datos", lambda: estado or ESTADO_OK)
    monkeypatch.setattr(
        sat69.database, "verificar_rfc", lambda rfc: {**(veredicto or VEREDICTO), "eco": rfc}
    )


def _get(client, params="?rfc=PRO950101AA1", headers=None):
    return client.get(f"/verificar{params}", headers=headers or {})


def _auth():
    return {"Authorization": f"Bearer {KEY}"}


def test_sin_bearer_no_pasa(app_oauth):
    with TestClient(app_oauth) as client:
        assert _get(client).status_code == 401
        assert _get(client, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_devuelve_el_veredicto_completo(app, monkeypatch):
    """No un booleano: el riesgo graduado y la situación. Un Presunto no es un
    Definitivo, y tratarlos igual en un expediente de defensa es un error caro."""
    _datos(monkeypatch)
    with TestClient(app) as client:
        r = _get(client, headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["riesgo"] == "CRITICO"
    assert body["en_69b"] is True
    assert body["eco"] == "PRO950101AA1"


def test_incluye_la_vigencia_del_dato(app, monkeypatch):
    """Contra qué corte del SAT se verificó es parte de la evidencia, no un extra."""
    _datos(monkeypatch)
    with TestClient(app) as client:
        r = _get(client, headers=_auth())
    assert r.json()["sat_actualizado_al"] == "2026-08-31"


def test_indice_vacio_es_503_no_un_falso_limpio(app, monkeypatch):
    """Sin datos, `verificar_rfc` diría LIMPIO de todo el mundo. Devolverlo con 200
    sería certificar como limpio a un EFOS definitivo."""
    _datos(monkeypatch, estado={"status": "no_data", "message": "sin datos"})
    with TestClient(app) as client:
        r = _get(client, headers=_auth())
    assert r.status_code == 503


def test_69b_vacio_aunque_el_69_tenga_datos_tambien_es_503(app, monkeypatch):
    """El matiz: `estado_datos()` dice `ok` si CUALQUIER tabla tiene filas. Si la
    del 69-B está vacía, el veredicto de EFOS es basura aunque el 69 esté completo."""
    _datos(
        monkeypatch,
        estado={"status": "ok", "art_69b": {"total_registros": 0, "sat_actualizado_al": None}},
    )
    with TestClient(app) as client:
        r = _get(client, headers=_auth())
    assert r.status_code == 503


def test_sin_rfc_es_400(app, monkeypatch):
    _datos(monkeypatch)
    with TestClient(app) as client:
        assert _get(client, params="", headers=_auth()).status_code == 400


# --- Lote ---


def _lote(client, rfcs, headers=None):
    return client.post("/verificar-lote", json={"rfcs": rfcs}, headers=headers or _auth())


def test_el_lote_devuelve_TODOS_no_solo_los_hallazgos(app, monkeypatch):
    """La diferencia deliberada con la tool `verificar_lote` del MCP, que devuelve
    sólo coincidencias. Esto le habla a un expediente de defensa: "lo verifiqué y no
    apareció" ES evidencia de diligencia y tiene que quedar registrada. Omitir los
    limpios es omitir la mitad de la prueba."""
    import sat69.database

    monkeypatch.setattr(sat69.database, "estado_datos", lambda: ESTADO_OK)
    monkeypatch.setattr(
        sat69.database,
        "verificar_rfc",
        lambda rfc: {"rfc": rfc, "riesgo": "CRITICO" if rfc.startswith("MAL") else "LIMPIO"},
    )
    with TestClient(app) as client:
        r = _lote(client, ["MAL010101AAA", "BIEN020202BBB", "BIEN030303CCC"])
    assert r.status_code == 200, r.text
    res = r.json()["resultados"]
    assert set(res) == {"MAL010101AAA", "BIEN020202BBB", "BIEN030303CCC"}
    assert res["BIEN020202BBB"]["riesgo"] == "LIMPIO", "el limpio tiene que venir"
    assert res["MAL010101AAA"]["riesgo"] == "CRITICO"


def test_cada_resultado_del_lote_trae_la_vigencia(app, monkeypatch):
    """Contra qué corte del SAT se verificó va en cada renglón, no sólo en la
    cabecera: cada uno se guarda como un evento aparte en la cronología."""
    import sat69.database

    monkeypatch.setattr(sat69.database, "estado_datos", lambda: ESTADO_OK)
    monkeypatch.setattr(
        sat69.database, "verificar_rfc", lambda rfc: {"rfc": rfc, "riesgo": "LIMPIO"}
    )
    with TestClient(app) as client:
        b = _lote(client, ["AAA010101AAA"]).json()
    assert b["sat_actualizado_al"] == "2026-08-31"
    assert b["resultados"]["AAA010101AAA"]["sat_actualizado_al"] == "2026-08-31"


def test_el_lote_respeta_el_tope(app, monkeypatch):
    import sat69.database

    monkeypatch.setattr(sat69.database, "estado_datos", lambda: ESTADO_OK)
    with TestClient(app) as client:
        r = _lote(client, [f"RFC{i:09d}" for i in range(501)])
    assert r.status_code == 400
    assert "500" in r.json()["error"]


def test_el_lote_sin_bearer_no_pasa(app_oauth):
    with TestClient(app_oauth) as client:
        assert client.post("/verificar-lote", json={"rfcs": ["AAA010101AAA"]}).status_code == 401


def test_el_lote_tambien_es_503_con_el_indice_vacio(app, monkeypatch):
    """Mismo guardia que la ruta individual: sin datos del 69-B, `verificar_rfc`
    diría LIMPIO de los 1,641 proveedores de golpe."""
    import sat69.database

    monkeypatch.setattr(sat69.database, "estado_datos", lambda: {"status": "no_data"})
    with TestClient(app) as client:
        assert _lote(client, ["AAA010101AAA"]).status_code == 503


def test_el_lote_pide_la_lista(app, monkeypatch):
    import sat69.database

    monkeypatch.setattr(sat69.database, "estado_datos", lambda: ESTADO_OK)
    with TestClient(app) as client:
        assert client.post("/verificar-lote", json={}, headers=_auth()).status_code == 400
        assert client.post("/verificar-lote", json={"rfcs": []}, headers=_auth()).status_code == 400
