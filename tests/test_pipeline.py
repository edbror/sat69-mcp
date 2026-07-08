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
    "1,AAA080808HL8,EMPRESA DEMO SA DE CV,Definitivo,of,01/06/2018,of,25/06/2018,,,,,ofd,27/09/2018,ofd,28/09/2018,,,,\n"
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
