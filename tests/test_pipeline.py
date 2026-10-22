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
    # Las cuatro listas cargadas, porque un LIMPIO con una lista vacía ya no es
    # LIMPIO: es NO_VERIFICADO. Este test aseveraba lo contrario y por eso dejaba
    # pasar el falso negativo del CSD.
    db.replace_69b_bis(pipeline.parse_69b_bis(_FIXTURE_69B_BIS.read_bytes())[0])
    db.replace_csd(pipeline.parse_csd(_FIXTURE_CSD.read_bytes())[0])

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


# --- Fuente congelada: el trap que costó 11 meses de datos del CSD ------------

def test_last_modified_se_lee_como_fecha_iso():
    from sat69 import fetcher

    # Formato RFC 1123, el que manda tanto omawww como el blob de Azure.
    assert fetcher._last_modified_iso("Wed, 03 Dec 2025 19:59:49 GMT") == "2025-12-03"
    assert fetcher._last_modified_iso("Mon, 19 Oct 2026 23:55:46 GMT") == "2026-10-19"
    assert fetcher._last_modified_iso("ayer como a las cuatro") is None
    assert fetcher._last_modified_iso(None) is None
    assert fetcher._last_modified_iso("") is None


def test_vigencia_iso_acepta_la_fecha_del_last_modified():
    # El 69 y el CSD no traen nota de vigencia, así que la suya ES el Last-Modified.
    # Sin esta rama el guard no podría comparar vigencias de esos archivos.
    assert pipeline._vigencia_iso("2026-10-19") == "2026-10-19"
    assert pipeline._vigencia_iso(" 2025-12-03 ") == "2025-12-03"
    assert pipeline._vigencia_iso("2026-13") is None


def test_guard_rechaza_un_archivo_mas_viejo_que_el_importado(fresh_db):
    """El caso real: la fuente del CSD quedó congelada en el corte del 3-dic-2025 y
    seguía devolviendo 200, así que el cron la reimportaba a diario sin que nada
    avisara. Con el Last-Modified como vigencia, retroceder ya truena."""
    src = "CSDsinefectos.csv"
    _sembrar(src, 59636, "2026-10-19")
    with pytest.raises(pipeline.ImportacionSospechosa, match="anterior a"):
        pipeline._verificar_frescura(src, [{}] * 59000, "2025-12-03")
    # y avanzar sigue pasando
    pipeline._verificar_frescura(src, [{}] * 59700, "2026-10-21")


def test_las_fuentes_apuntan_al_blob_vivo_y_no_a_omawww():
    """Candado de regresión. `omawww.sat.gob.mx` responde 200 con archivos
    congelados —el 69-B en julio, el 69 y el CSD en octubre— así que volver a
    apuntar ahí no se nota hasta que alguien reporta un proveedor como limpio.
    La página de datos abiertos del SAT linkea el blob, no omawww."""
    from sat69.config import settings

    fuentes = (settings.url_69b, settings.url_69b_bis, settings.url_csd, settings.base_69)
    for u in fuentes:
        assert "omawww" not in u, f"fuente congelada: {u}"
    assert "No_localizados.csv" in pipeline.settings.files_69, (
        "en el blob se llama No_localizados.csv; el nombre viejo da 404"
    )
    assert "Condonados.csv" not in pipeline.settings.files_69, (
        "el SAT ya no publica Condonados.csv; lo partió en cinco archivos"
    )


# --- Una lista vacía no se declara limpia ------------------------------------

def test_una_lista_sin_datos_no_se_declara_limpia(fresh_db):
    """El falso negativo que de verdad pasó: con la tabla del CSD en cero, un RFC
    con el sello cancelado desde 2024 salía LIMPIO, y el texto del veredicto
    afirmaba haber revisado las cuatro listas. Un consumidor lo guarda como
    "proveedor verificado sin hallazgos", que es una constancia falsa."""
    db.replace_69b(pipeline.parse_69b(CSV_69B)[0])  # sólo el 69-B tiene datos

    r = db.verificar_rfc("XAXX010101000")
    assert r["riesgo"] == "NO_VERIFICADO", r
    assert "CSD sin efectos" in r["listas_sin_datos"], r
    assert "Art. 69" in r["listas_sin_datos"], r
    assert "limpio" not in r["veredicto"].lower().replace("nunca 'limpio'", "")


def test_con_las_cuatro_listas_cargadas_el_limpio_vuelve(fresh_db):
    db.replace_69b(pipeline.parse_69b(CSV_69B)[0])
    db.replace_69_file("Firmes.csv", pipeline.parse_69(CSV_69, "Firmes.csv"))
    db.replace_69b_bis(pipeline.parse_69b_bis(_FIXTURE_69B_BIS.read_bytes())[0])
    db.replace_csd(pipeline.parse_csd(_FIXTURE_CSD.read_bytes())[0])

    r = db.verificar_rfc("XAXX010101000")
    assert r["riesgo"] == "LIMPIO", r
    assert r["listas_sin_datos"] == []


def test_un_hallazgo_real_no_se_degrada_por_otra_lista_vacia(fresh_db):
    """Sólo el LIMPIO se degrada. Si el RFC aparece en una lista que SÍ se pudo
    consultar, el veredicto ya dice algo verdadero y taparlo sería peor."""
    db.replace_69b(pipeline.parse_69b(CSV_69B)[0])  # las otras tres vacías

    r = db.verificar_rfc("AAA080808HL8")  # Definitivo en el fixture
    assert r["riesgo"] == "CRITICO", r
    assert r["en_69b"] is True
    assert r["listas_sin_datos"], "el dato de qué faltó se reporta igual"


def test_el_repr_de_la_config_no_imprime_las_llaves():
    """Un dataclass imprime todos sus campos, así que una aserción fallida, un log
    de la config o una excepción que la arrastre publicaba las llaves en claro.
    Pasó: una aserción de pytest escupió `gemini_key` completa en la salida.

    Los campos NO se leen de `_SECRETOS`: se derivan del nombre. Si se leyeran de
    ahí, el test probaría su propia premisa y quitar un campo del set lo dejaría
    pasar — lo comprobé, pasaba. Así, una llave nueva que nadie registre truena.
    """
    from dataclasses import fields, replace

    from sat69.config import Settings, settings

    pistas = ("key", "token", "secret", "password", "proxy")
    sensibles = [
        f.name for f in fields(Settings) if any(p in f.name.lower() for p in pistas)
    ]
    assert len(sensibles) >= 6, f"se esperaban varios campos sensibles, hay {sensibles}"

    falsos = {c: f"llave-secreta-{c}" for c in sensibles}
    texto = repr(replace(settings, **falsos))
    for campo, valor in falsos.items():
        assert valor not in texto, f"{campo} salió en claro; agrégalo a _SECRETOS"
        assert campo in texto, f"{campo} debe seguir apareciendo como presente/ausente"
    # y lo que no es secreto sí se ve, que para eso existe el repr
    assert "Documents_AGR" in texto


def test_el_last_modified_de_la_descarga_llega_hasta_la_vigencia_guardada(
    fresh_db, monkeypatch
):
    """El cableado, no el mecanismo. `_last_modified_iso` y `_verificar_frescura`
    ya tienen test cada uno, y aun así la suite seguía verde con `fetch_csv`
    devolviendo None: nada probaba que la cabecera de la descarga de verdad
    llegara a `sat_actualizado_al`. Sin eso, el 69 y el CSD vuelven a quedarse
    sin señal de vigencia y una fuente congelada otra vez se ve igual que una
    que no cambió. Se intercepta en httpx, no en `fetch_csv`, porque stubbear
    `fetch_csv` apagaría justo lo que hay que probar.
    """
    import httpx

    class _Resp:
        headers = {"last-modified": "Mon, 19 Oct 2026 23:55:46 GMT"}
        content = _FIXTURE_CSD.read_bytes()

        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx.Client, "get", lambda self, url: _Resp())

    res = pipeline._import_csd(force=True)
    assert res["sat_actualizado_al"] == "2026-10-19", res
    assert db.get_source_state("CSDsinefectos.csv")["sat_actualizado_al"] == "2026-10-19"

    # Y la segunda pasada con un archivo más viejo se rechaza: es el caso real.
    _Resp.headers = {"last-modified": "Wed, 03 Dec 2025 19:59:49 GMT"}
    with pytest.raises(pipeline.ImportacionSospechosa, match="anterior a"):
        pipeline._import_csd(force=True)


def test_la_merma_se_acepta_solo_con_la_escotilla_explicita(fresh_db, monkeypatch):
    """La migración de omawww al blob encogió `Exigibles.csv` de 5,535 a 2,747
    filas, y era legítimo: 691 RFC nuevos y 3,479 dados de baja (una descarga
    truncada no trae registros nuevos). El guard igual la frenó, que es lo
    correcto por defecto. La salida es una decisión de operador."""
    from dataclasses import replace

    src = "Exigibles.csv"
    _sembrar(src, 5535, "2026-01-17")

    with pytest.raises(pipeline.ImportacionSospechosa, match="merma"):
        pipeline._verificar_frescura(src, [{}] * 2747, "2026-10-19")

    monkeypatch.setattr(pipeline, "settings", replace(pipeline.settings, permitir_merma=True))
    pipeline._verificar_frescura(src, [{}] * 2747, "2026-10-19")

    # La escotilla NO tapa los otros dos: cero filas y vigencia que retrocede
    # siguen siendo rechazo, porque ninguno de los dos se explica migrando.
    with pytest.raises(pipeline.ImportacionSospechosa, match="0 filas"):
        pipeline._verificar_frescura(src, [], "2026-10-19")
    with pytest.raises(pipeline.ImportacionSospechosa, match="anterior a"):
        pipeline._verificar_frescura(src, [{}] * 5535, "2025-12-03")


def test_un_archivo_del_69_que_se_retira_no_deja_filas_zombi(fresh_db):
    """`replace_69_file` reemplaza POR archivo, así que renombrar una fuente dejaba
    las filas viejas vivas. Pasó al migrar al blob: `NoLocalizados.csv` se volvió
    `No_localizados.csv` y el total del 69 contaba doble. Y `verificar_rfc` no
    filtra por archivo, así que un RFC dado de baja seguía apareciendo desde el
    archivo retirado: un falso positivo sobre un contribuyente, por dato zombi."""
    viejo = pipeline.parse_69(
        (
            "RFC,RAZON SOCIAL,TIPO PERSONA,SUPUESTO,FECHA,ENTIDAD\n"
            "ZOM010101AA1,FANTASMA SA,M,NO LOCALIZADOS,01/01/2020,JALISCO\n"
        ).encode("latin-1"),
        "NoLocalizados.csv",
    )
    db.replace_69_file("NoLocalizados.csv", viejo)
    db.replace_69_file("Firmes.csv", pipeline.parse_69(CSV_69, "Firmes.csv"))
    db.record_source("69", "NoLocalizados.csv", "h1", 1, "2026-01-01T00:00:00Z")
    db.record_source("69", "Firmes.csv", "h2", 1, "2026-01-01T00:00:00Z")

    assert db.verificar_rfc("ZOM010101AA1")["en_69"] is True, "precondición"

    retirados = db.purgar_archivos_69_retirados(("Firmes.csv", "No_localizados.csv"))
    assert retirados == [{"source_file": "NoLocalizados.csv", "filas": 1}], retirados

    assert db.verificar_rfc("ZOM010101AA1")["en_69"] is False, "quedó el dato zombi"
    assert db.verificar_rfc("AAG090703QT6")["en_69"] is True, "no se llevó lo vigente"
    assert db.get_source_state("NoLocalizados.csv") is None
    assert db.get_source_state("Firmes.csv") is not None


def test_si_una_descarga_del_69_falla_no_se_purga_nada(fresh_db, monkeypatch):
    """La purga corre después de importar, y sólo si todo entró. Si una descarga
    falló y purgáramos igual, borraríamos el archivo retirado sin tener el nuevo:
    el 69 se queda con un hueco y `verificar_rfc` responde que un RFC publicado no
    aparece. Vale más una lista con un nombre viejo que una lista incompleta."""
    import httpx

    from sat69 import fetcher

    zombi = pipeline.parse_69(
        (
            "RFC,RAZON SOCIAL,TIPO PERSONA,SUPUESTO,FECHA,ENTIDAD\n"
            "ZOM010101AA1,FANTASMA SA,M,NO LOCALIZADOS,01/01/2020,JALISCO\n"
        ).encode("latin-1"),
        "NoLocalizados.csv",
    )
    db.replace_69_file("NoLocalizados.csv", zombi)

    def _siempre_truena(self, url):
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(httpx.Client, "get", _siempre_truena)
    monkeypatch.setattr(fetcher.time, "sleep", lambda _s: None)  # sin backoff real

    res = pipeline.process_import("69", force_refresh=True)
    assert res["errores"], "la descalabrada tenía que reportarse"
    assert db.verificar_rfc("ZOM010101AA1")["en_69"] is True, (
        "se purgó la lista vieja sin haber traído la nueva"
    )


def test_un_indice_a_medias_no_se_declara_limpio(fresh_db):
    """El caso que de verdad muerde, porque no se ve. `push_to_turso` hace DELETE y
    luego INSERT por lotes, así que un `pull` que cae en medio se trae un pedazo y
    el servicio lo sirve como completo. Pasó dos veces en producción: el 18-oct
    importó 15,024 de 557,094 filas y el 22-oct 75,847, y las dos veces
    `verificar_rfc` contestó LIMPIO para todo lo que faltaba, sin avisar.

    La tabla NO está vacía, así que el guard de vacías no lo atrapa. El contraste
    es contra lo que `source_files` declara.
    """
    db.replace_69b(pipeline.parse_69b(CSV_69B)[0])
    db.replace_69b_bis(pipeline.parse_69b_bis(_FIXTURE_69B_BIS.read_bytes())[0])
    db.replace_csd(pipeline.parse_csd(_FIXTURE_CSD.read_bytes())[0])
    db.replace_69_file("Firmes.csv", pipeline.parse_69(CSV_69, "Firmes.csv"))

    # Con las cuentas cuadradas, un RFC fuera de las listas es LIMPIO.
    for src, ds, n in (
        ("Listado_Completo_69-B.csv", "69b", 1), ("Listado_69_B_Bis_Completo.csv", "69bbis", 3),
        ("CSDsinefectos.csv", "csd", 3), ("Firmes.csv", "69", 1),
    ):
        db.record_source(ds, src, "h", n, "2026-10-22T00:00:00Z")
    assert db.verificar_rfc("XAXX010101000")["riesgo"] == "LIMPIO"

    # Ahora `source_files` declara 557,094 del 69 y la tabla tiene 1: pull a medias.
    db.record_source("69", "Firmes.csv", "h", 557094, "2026-10-22T00:00:00Z")
    r = db.verificar_rfc("XAXX010101000")
    assert r["riesgo"] == "NO_VERIFICADO", r
    assert any("Art. 69" in x for x in r["listas_sin_datos"]), r
    assert "557,094" in r["veredicto"], "el veredicto dice cuánto falta"


def test_el_margen_del_indice_tolera_una_diferencia_chica(fresh_db):
    """El parser descarta filas sin RFC, así que real y declarado pueden no cuadrar
    exactamente. Un 1% de diferencia no es un pull a medias; un 90% sí."""
    db.replace_69b(pipeline.parse_69b(CSV_69B)[0])
    db.replace_69b_bis(pipeline.parse_69b_bis(_FIXTURE_69B_BIS.read_bytes())[0])
    db.replace_csd(pipeline.parse_csd(_FIXTURE_CSD.read_bytes())[0])
    csv_mil = (
        "RFC,RS,TP,SUPUESTO,FECHA,ENT\n"
        + "".join(f"AAA{i:06d}XX1,EMPRESA,M,FIRMES,01/01/2020,JALISCO\n" for i in range(1000))
    ).encode("latin-1")
    db.replace_69_file("Firmes.csv", pipeline.parse_69(csv_mil, "Firmes.csv"))
    for src, ds, n in (
        ("Listado_Completo_69-B.csv", "69b", 1), ("Listado_69_B_Bis_Completo.csv", "69bbis", 3),
        ("CSDsinefectos.csv", "csd", 3),
    ):
        db.record_source(ds, src, "h", n, "2026-10-22T00:00:00Z")

    db.record_source("69", "Firmes.csv", "h", 1010, "2026-10-22T00:00:00Z")   # -1%
    assert db.verificar_rfc("XAXX010101000")["riesgo"] == "LIMPIO"

    db.record_source("69", "Firmes.csv", "h", 10000, "2026-10-22T00:00:00Z")  # -90%
    assert db.verificar_rfc("XAXX010101000")["riesgo"] == "NO_VERIFICADO"
