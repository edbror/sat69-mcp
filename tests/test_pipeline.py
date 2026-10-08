"""Pruebas de parseo e ingesta contra fixtures en memoria (sin red)."""
from pathlib import Path

import pytest

from sat69 import database as db
from sat69 import pipeline

# CSV 69-B mínimo: 2 líneas de nota + encabezado + 1 fila (20 columnas).
CSV_69B = (
    "Informacion actualizada al 31 de diciembre de 2025; nota.\n"
    "Listado completo de contribuyentes (Articulo 69-B del CFF),\n"
    "No,RFC,Nombre,Situacion,c4,c5,c6,c7,c8,c9,c10,c11,c12,c13,c14,c15,c16,c17,c18,c19\n"
    # una sola fila de 20 columnas, partida para no pasar de 100 caracteres
    "1,AAA080808HL8,EMPRESA DEMO SA DE CV,Definitivo,of,01/06/2018,of,25/06/2018,"
    ",,,,ofd,27/09/2018,ofd,28/09/2018,,,,\n"
).encode("latin-1")

# CSV 69 mínimo: encabezado + 1 fila (6 columnas).
CSV_69 = (
    "RFC,RAZON SOCIAL,TIPO PERSONA,SUPUESTO,FECHA DE PRIMERA PUBLICACION,ENTIDAD FEDERATIVA\n"
    "AAG090703QT6,APLICA SA DE CV,M,FIRMES,01/01/2014,CIUDAD DE MEXICO\n"
).encode("latin-1")


@pytest.fixture()
def fresh_db(tmp_path: Path):
    db.configure(None)  # reset
    db.init_db(tmp_path / "test.db")
    yield


def test_parse_69b_extrae_vigencia_y_fila():
    rows, vigencia = pipeline.parse_69b(CSV_69B)
    assert vigencia == "31 de diciembre de 2025"
    assert len(rows) == 1
    assert rows[0]["rfc"] == "AAA080808HL8"
    assert rows[0]["situacion"] == "Definitivo"
    assert rows[0]["publicacion_dof_definitivos"] == "2018-09-28"


def test_parse_69_mapea_columnas():
    rows = pipeline.parse_69(CSV_69, "Firmes.csv")
    assert len(rows) == 1
    assert rows[0]["rfc"] == "AAG090703QT6"
    assert rows[0]["supuesto"] == "FIRMES"
    assert rows[0]["fecha_primera_publicacion"] == "2014-01-01"


def test_carga_y_veredicto(fresh_db):
    rows_b, _ = pipeline.parse_69b(CSV_69B)
    db.replace_69b(rows_b)
    db.replace_69_file("Firmes.csv", pipeline.parse_69(CSV_69, "Firmes.csv"))

    r = db.verificar_rfc("aaa080808hl8")            # normaliza
    assert r["riesgo"] == "CRITICO"
    assert r["en_69b"] is True

    limpio = db.verificar_rfc("XAXX010101000")
    assert limpio["riesgo"] == "LIMPIO"


def test_buscar_nombre_fts(fresh_db):
    db.replace_69_file("Firmes.csv", pipeline.parse_69(CSV_69, "Firmes.csv"))
    res = db.buscar_nombre("aplica", "69")
    assert res["69"] and res["69"][0]["rfc"] == "AAG090703QT6"


# --- 69-B Bis: parseo contra el archivo real del SAT (fixture) ---------------
_FIXTURE_69B_BIS = Path(__file__).parent / "fixtures" / "69b_bis_sample.csv"


def test_parse_69b_bis_fixture_real():
    raw = _FIXTURE_69B_BIS.read_bytes()
    rows, vigencia = pipeline.parse_69b_bis(raw)
    assert vigencia == "05 de junio de 2026"
    assert len(rows) == 3
    por_rfc = {r["rfc"]: r for r in rows}
    assert por_rfc["CPH061010RB7"]["situacion"] == "Definitivo"
    assert por_rfc["CPH061010RB7"]["publicacion_dof_definitivo"] == "2024-07-05"
    # el nombre venía con salto de línea embebido → se normaliza a una sola línea.
    assert "\n" not in por_rfc["CPH061010RB7"]["nombre"]
    assert por_rfc["OAN151230HWA"]["situacion"] == "Sentencia Favorable"


def test_carga_y_veredicto_69b_bis(fresh_db):
    rows, _ = pipeline.parse_69b_bis(_FIXTURE_69B_BIS.read_bytes())
    db.replace_69b_bis(rows)

    r = db.verificar_rfc("cph061010rb7")            # Definitivo 69-B Bis
    assert r["riesgo"] == "MEDIO"
    assert r["en_69b_bis"] is True
    assert r["en_69b"] is False

    fav = db.verificar_rfc("OAN151230HWA")          # Sentencia Favorable
    assert fav["riesgo"] == "BAJO"


def test_buscar_nombre_69b_bis(fresh_db):
    db.replace_69b_bis(pipeline.parse_69b_bis(_FIXTURE_69B_BIS.read_bytes())[0])
    res = db.buscar_nombre("bernabastos", "69bbis")
    assert res["69b_bis"] and res["69b_bis"][0]["rfc"] == "BER160621KN5"


# --- CSD sin efectos: parseo contra el archivo real del SAT (fixture) --------
_FIXTURE_CSD = Path(__file__).parent / "fixtures" / "csd_sample.csv"


def test_parse_csd_fixture_real():
    rows, vigencia = pipeline.parse_csd(_FIXTURE_CSD.read_bytes())
    assert vigencia is None            # el archivo del SAT no trae nota de vigencia
    assert len(rows) == 3
    por_rfc = {r["rfc"]: r for r in rows}
    assert por_rfc["GTM870825HA7"]["supuesto"] == "FRACCIÓN X"
    assert por_rfc["GTM870825HA7"]["fecha_cancelacion"] == "2024-01-05"
    assert por_rfc["GTM870825HA7"]["fecha_publicacion"] == "2024-07-23"
    # la fila con coma entrecomillada se parsea como un solo campo (CSV real, no split)
    assert por_rfc["AAA1210097V4"]["nombre"] == "AQUO, ABOGADOS ASOCIADOS SC"


def test_carga_y_veredicto_csd(fresh_db):
    rows, _ = pipeline.parse_csd(_FIXTURE_CSD.read_bytes())
    db.replace_csd(rows)

    r = db.verificar_rfc("gtm870825ha7")            # CSD sin efectos
    assert r["riesgo"] == "MEDIO"
    assert r["en_csd"] is True
    assert r["en_69b"] is False
    assert r["registros_csd"][0]["supuesto"] == "FRACCIÓN X"


def test_buscar_nombre_csd(fresh_db):
    db.replace_csd(pipeline.parse_csd(_FIXTURE_CSD.read_bytes())[0])
    res = db.buscar_nombre("aquo", "csd")
    assert res["csd"] and res["csd"][0]["rfc"] == "AAA1210097V4"


# ---------------------------------------------------------------------------
# Guard de frescura: una descarga corta, vacía o vieja no pisa datos buenos.
# ---------------------------------------------------------------------------

def test_vigencia_iso_tolera_el_mes_como_lo_escriba_el_sat():
    # el SAT mezcla mayúscula y minúscula entre archivos: "31 de Agosto" vs "31 de mayo"
    assert pipeline._vigencia_iso("31 de Agosto de 2026") == "2026-08-31"
    assert pipeline._vigencia_iso("31 de mayo de 2026") == "2026-05-31"
    assert pipeline._vigencia_iso("23 de septiembre de 2026") == "2026-09-23"
    assert pipeline._vigencia_iso("1 de enero de 2026") == "2026-01-01"
    assert pipeline._vigencia_iso("el martes pasado") is None
    assert pipeline._vigencia_iso(None) is None
    # ordena como fecha, no como texto: mayo < agosto aunque "mayo" > "agosto"
    assert pipeline._vigencia_iso("31 de mayo de 2026") < pipeline._vigencia_iso(
        "31 de Agosto de 2026"
    )


def test_guard_deja_pasar_la_primera_importacion(fresh_db):
    # sin estado previo no hay nada que proteger
    pipeline._verificar_frescura("Listado_Completo_69-B.csv", [], None)


def _sembrar(src: str, filas: int, vigencia: str | None) -> None:
    db.record_source("69b", src, "hash-viejo", filas, "2026-01-01T00:00:00Z",
                     sat_actualizado_al=vigencia)


def test_guard_rechaza_cero_filas(fresh_db):
    src = "Listado_Completo_69-B.csv"
    _sembrar(src, 14837, "31 de Agosto de 2026")
    with pytest.raises(pipeline.ImportacionSospechosa, match="0 filas"):
        pipeline._verificar_frescura(src, [], "31 de Agosto de 2026")


def test_guard_rechaza_merma_grande_y_acepta_la_chica(fresh_db):
    src = "Firmes.csv"
    _sembrar(src, 1000, None)
    with pytest.raises(pipeline.ImportacionSospechosa, match="merma"):
        pipeline._verificar_frescura(src, [{}] * 800, None)      # -20%
    pipeline._verificar_frescura(src, [{}] * 950, None)          # -5%, normal
    pipeline._verificar_frescura(src, [{}] * 1200, None)         # crecer siempre pasa


def test_guard_rechaza_vigencia_que_retrocede(fresh_db):
    src = "Listado_Completo_69-B.csv"
    _sembrar(src, 100, "31 de Agosto de 2026")
    with pytest.raises(pipeline.ImportacionSospechosa, match="anterior a"):
        pipeline._verificar_frescura(src, [{}] * 100, "31 de mayo de 2026")
    pipeline._verificar_frescura(src, [{}] * 100, "23 de septiembre de 2026")


def test_guard_no_estorba_a_una_lista_diminuta(fresh_db):
    # el 69-B Bis nacional son 3 filas: un MIN_FILAS absoluto la habría bloqueado
    src = "Listado_69_B_Bis_Completo.csv"
    _sembrar(src, 3, "23 de septiembre de 2026")
    pipeline._verificar_frescura(src, [{}] * 3, "23 de septiembre de 2026")
    pipeline._verificar_frescura(src, [{}] * 4, "30 de septiembre de 2026")
    with pytest.raises(pipeline.ImportacionSospechosa):
        pipeline._verificar_frescura(src, [], "23 de septiembre de 2026")
