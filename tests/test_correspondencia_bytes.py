"""Si cayeron filas, el archivo del SAT tuvo que adelgazar con ellas.

**El hueco que esto cubre, y por qué los dos guards que ya había no lo ven.**

Todo lo demás en `source_files` sale de lo que nosotros parseamos: `rows` son las
filas insertadas. Así que `listas_incompletas()` detecta un pull truncado —bajó
la tabla y no lo declarado— pero no un PARSEO truncado, donde bajan las dos
juntas y siguen cuadrando. Ése es un falso negativo: `verificar_rfc` contestaría
LIMPIO para contribuyentes que sí están en la lista.

Y `MERMA_MAX` es 0.10, un umbral sobre el total: mil filas perdidas de 264,766
son 0.38% y pasan de largo.

Los bytes del archivo son la única cifra que viene del origen, así que no se
mueven cuando el que falla somos nosotros. Los números de abajo son los reales
del 2026-11-18, el día que investigar una baja de 26 filas destapó esto.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sat69 import database as db
from sat69 import pipeline
from sat69.config import settings


@pytest.fixture()
def base(tmp_path: Path):
    db.configure(None)
    db.init_db(tmp_path / "t.db")
    yield


def _previo(filas: int, bytes_origen: int) -> None:
    db.record_source("69", "Firmes.csv", "hash-viejo", filas, "2026-11-18T03:41:00Z",
                     sat_actualizado_al="2026-11-18", bytes_origen=bytes_origen)


def _filas(n: int) -> list[dict]:
    return [{"rfc": f"AAA{i:06d}XX1"} for i in range(n)]


# Los tres descensos REALES del 2026-11-18: el SAT quitó gente y el archivo
# adelgazó en proporción. Los tres tienen que pasar.
@pytest.mark.parametrize(("archivo", "b_antes", "f_antes", "b_ahora", "f_ahora"), [
    ("Firmes",         20147192, 264783, 20145964, 264766),
    ("No_localizados",  4440044,  53383,  4439453,  53376),
    ("Cancelados",     20675129, 185320, 20674876, 185318),
])
def test_una_baja_real_del_sat_pasa(base, archivo, b_antes, f_antes, b_ahora, f_ahora):
    """Si el guard reprobara aquí, el cron quedaría rojo cada vez que el SAT
    da de baja a alguien, que es algo que hace."""
    prev = {"rows": f_antes, "bytes": b_antes, "sat_actualizado_al": "2026-11-18"}
    pipeline._verificar_correspondencia(archivo, b"x" * b_ahora, _filas(f_ahora), prev)


# Y lo que tiene que atrapar: filas que desaparecen sin que el archivo cambie.
@pytest.mark.parametrize("perdidas", [10, 100, 1000, 50000])
def test_filas_perdidas_a_bytes_constantes_se_atrapan(base, perdidas):
    """**La prueba que importa.** Diez filas de 264,783 son 0.0038%: `MERMA_MAX`
    de 0.10 no las ve ni de lejos, y el contraste de `listas_incompletas()`
    tampoco, porque lo declarado baja junto con la tabla. Esto sí."""
    prev = {"rows": 264783, "bytes": 20147192, "sat_actualizado_al": "2026-11-18"}
    with pytest.raises(pipeline.ImportacionSospechosa, match="adelgazó"):
        pipeline._verificar_correspondencia(
            "Firmes.csv", b"x" * 20147192, _filas(264783 - perdidas), prev
        )


def test_se_atrapa_aunque_el_sat_tambien_haya_quitado_filas(base):
    """El caso mezclado, que es el realista: el SAT quitó 17 y el parser perdió
    1,000. El archivo adelgazó 1,228 bytes, pero 1,017 filas pesaban ~77,000."""
    prev = {"rows": 264783, "bytes": 20147192, "sat_actualizado_al": "2026-11-18"}
    with pytest.raises(pipeline.ImportacionSospechosa, match="adelgazó"):
        pipeline._verificar_correspondencia(
            "Firmes.csv", b"x" * 20145964, _filas(264783 - 1017), prev
        )


def test_sin_bytes_previos_no_opina(base, caplog):
    """Los archivos importados antes de que la columna existiera no tienen línea
    base. El guard EMPIEZA a proteger en la corrida siguiente, y lo dice en el
    log en vez de aparentar que ya vigila."""
    prev = {"rows": 264783, "bytes": None, "sat_actualizado_al": "2026-11-18"}
    pipeline._verificar_correspondencia("Firmes.csv", b"x" * 100, _filas(1000), prev)
    assert any("sin bytes previos" in r.message for r in caplog.records)


def test_si_el_archivo_crece_no_opina(base):
    """Más filas no es este modo de falla."""
    prev = {"rows": 100, "bytes": 8000, "sat_actualizado_al": "2026-11-18"}
    pipeline._verificar_correspondencia("Firmes.csv", b"x" * 8000, _filas(120), prev)


def test_la_escotilla_de_operador_lo_deja_pasar_pero_grita(base, monkeypatch, caplog):
    """Un cambio de formato del origen rompe la correspondencia de forma legítima
    y el código no puede saberlo, así que la salida es la misma que la de la
    merma: explícita, de operador, y ruidosa en el log."""
    monkeypatch.setattr(pipeline, "settings", dataclasses.replace(settings, permitir_merma=True))
    prev = {"rows": 264783, "bytes": 20147192, "sat_actualizado_al": "2026-11-18"}
    pipeline._verificar_correspondencia(
        "Firmes.csv", b"x" * 20147192, _filas(263783), prev
    )
    assert any("SAT_PERMITIR_MERMA" in r.message for r in caplog.records)


def test_el_cableado_llega_hasta_la_base(base, monkeypatch):
    """El mecanismo tiene sus tests arriba; esto prueba el CABLE, que es lo que
    se rompe en silencio. Si `record_source` no recibiera los bytes, el guard
    nunca tendría línea base y todo lo de arriba seguiría verde."""
    import httpx

    fixture = Path(__file__).parent / "fixtures" / "csd_sample.csv"

    class _Resp:
        headers = {"last-modified": "Mon, 19 Oct 2026 23:55:46 GMT"}
        content = fixture.read_bytes()

        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx.Client, "get", lambda self, url: _Resp())
    pipeline._import_csd(force=True)

    estado = db.get_source_state("CSDsinefectos.csv")
    assert estado["bytes"] == len(fixture.read_bytes()), (
        f"los bytes del origen no llegaron a source_files: {estado}"
    )


def test_el_guard_esta_cableado_a_la_importacion(base, monkeypatch):
    """El sabotaje que todo lo de arriba deja pasar: borrar la LLAMADA al guard.

    Los tests anteriores invocan `_verificar_correspondencia` a mano, así que si
    se quita la línea que `_verificar_frescura` usa para llamarlo, los doce
    siguen en verde y el candado no vigila nada. Esto entra por donde entra el
    cron: `_import_csd`.

    La pérdida es de una fila en 300 —0.33%—, bien por debajo de `MERMA_MAX`, así
    que el único candado que puede verla es el de correspondencia. Si el cable se
    corta, la segunda importación pasa en verde y pisa los datos buenos.
    """
    import httpx

    filas = 300
    csv_sat = (
        "RFC,NOMBRE,SUPUESTO,FECHA CANCELACION,ADMON,FECHA PUBLICACION\n"
        + "".join(
            f"AAA{i:06d}XX1,CONTRIBUYENTE {i},FRACCION X,05/01/2024,AGR,23/07/2024\n"
            for i in range(filas)
        )
    ).encode(settings.source_encoding)

    class _Resp:
        headers = {"last-modified": "Mon, 19 Oct 2026 23:55:46 GMT"}
        content = csv_sat

        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx.Client, "get", lambda self, url: _Resp())

    # Primera corrida: deja la línea base, filas y bytes.
    pipeline._import_csd(force=True)
    assert db.get_source_state("CSDsinefectos.csv")["rows"] == filas

    # Segunda: el MISMO archivo, byte por byte, pero el parser se come una fila.
    real = pipeline.parse_csd
    monkeypatch.setattr(pipeline, "parse_csd", lambda raw: (real(raw)[0][:-1], None))
    with pytest.raises(pipeline.ImportacionSospechosa, match="adelgazó"):
        pipeline._import_csd(force=True)


def test_ninguna_ruta_de_importacion_se_queda_sin_pasar_los_bytes():
    """`raw` es opcional en `_verificar_frescura`, y eso tiene un filo.

    Es opcional porque los otros dos candados —merma y vigencia— no lo necesitan
    y dieciocho pruebas los ejercitan con tres argumentos. El filo: una ruta de
    importación nueva que olvide `raw=raw` desactiva la correspondencia **sólo
    para ese archivo**, en silencio y en verde.

    El test de arriba entra por `_import_csd`, que es una de las cuatro. Esto
    cubre las cuatro y las que vengan, leyendo el árbol en vez de la cadena: el
    código es el que tiene que decir quién pasa los bytes, no un grep que una
    línea partida en dos deja mentir.
    """
    import ast

    fuente = Path(pipeline.__file__).read_text(encoding="utf-8")
    arbol = ast.parse(fuente)

    # Las funciones que importan un archivo del SAT: las que llaman a record_source.
    importadoras = {
        nodo.name: nodo
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.FunctionDef)
        and any(
            isinstance(c.func, ast.Attribute) and c.func.attr == "record_source"
            for c in ast.walk(nodo)
            if isinstance(c, ast.Call)
        )
    }
    assert len(importadoras) >= 4, f"¿cambió el recorrido? encontré {list(importadoras)}"

    for nombre, nodo in importadoras.items():
        llamadas = [
            c for c in ast.walk(nodo)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "_verificar_frescura"
        ]
        assert llamadas, f"{nombre} importa un archivo y no verifica frescura"
        for c in llamadas:
            pasa_raw = any(k.arg == "raw" for k in c.keywords) or len(c.args) >= 4
            assert pasa_raw, (
                f"{nombre} llama a _verificar_frescura sin `raw`: la "
                f"correspondencia de bytes queda apagada para ese archivo, en "
                f"silencio y en verde."
            )

        pasa_bytes = any(
            k.arg == "bytes_origen"
            for c in ast.walk(nodo)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr == "record_source"
            for k in c.keywords
        )
        assert pasa_bytes, (
            f"{nombre} guarda en source_files sin `bytes_origen`: sin línea base "
            f"el guard no puede comparar nada en la corrida siguiente."
        )
