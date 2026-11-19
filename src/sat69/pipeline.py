"""
Pipeline de ingesta SAT → SQLite (+ Turso).

Análogo a `process_agenda` del MCP de movilizaciones, pero sin OCR: los CSV del
SAT ya vienen estructurados. El parseo está separado de la descarga para poder
probarlo con archivos locales.

Flujo:
  descarga CSV (proxy opcional) → SHA-256 (omite si no cambió salvo force) →
  decodifica Latin-1 → parsea → reemplaza en SQLite → push a Turso.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import UTC, datetime

from . import database as db
from .config import settings
from .fetcher import fetch_csv, sha256

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _fecha(v: str | None) -> str | None:
    """dd/mm/aaaa → aaaa-mm-dd, o None."""
    v = (v or "").strip()
    if v in ("", "--", "N/A"):
        return None
    try:
        return datetime.strptime(v, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Guard de frescura
# ---------------------------------------------------------------------------
# Una descarga corta, vacía o vieja NO debe pisar datos buenos: el reemplazo es
# destructivo y la fuente es de un tercero que ya nos ha servido archivos raros.
# Lección heredada del MCP de REPSE, que lo agregó tras ver esto de cerca.
#
# El piso es RELATIVO a propósito. Un `MIN_FILAS` absoluto no sirve aquí: el
# 69-B Bis son 3 filas en todo el país y el 69 son cientos de miles, así que
# cualquier constante estaría mal para uno de los dos. Perder fracción de lo que
# ya había significa lo mismo en ambas escalas.
MERMA_MAX = 0.10

# Qué fracción de la caída de bytes esperada basta para creerle a una caída de
# filas. Ver `_verificar_correspondencia`: con 0.30 se acepta que el archivo nuevo
# pese hasta 70% MENOS de lo que la aritmética predice, que es holgura de sobra
# para filas de ancho desigual, y sigue atrapando una pérdida de diez filas a
# bytes constantes. Medido contra las tres bajas reales del 2026-11-18, los
# ratios fueron 0.95, 1.02 y 1.13.
CORRESPONDENCIA_MIN = 0.30

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


class ImportacionSospechosa(RuntimeError):
    """El archivo descargado no pasa el guard: se conserva lo anterior.

    `process_import` ya atrapa por archivo, así que esto degrada a un error en
    `errores[]` y los demás listados siguen importando.
    """


def _vigencia_iso(v: str | None) -> str | None:
    """"31 de Agosto de 2026" → "2026-08-31". None si no se puede leer.

    El SAT escribe el mes con mayúscula inicial o sin ella, según el archivo y
    el día — por eso se compara en minúsculas y no se confía en el formato.

    Acepta además una fecha ya en ISO, que es la forma en que llega la vigencia
    del 69 y del CSD: esos archivos no traen nota, así que su vigencia es el
    `Last-Modified` de la descarga.
    """
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", (v or "").strip()):
        return v.strip()
    m = re.search(r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóú]+)\s+de\s+(\d{4})", v or "")
    if not m:
        return None
    mes = MESES.get(m.group(2).lower())
    if mes is None:
        return None
    return f"{int(m.group(3)):04d}-{mes:02d}-{int(m.group(1)):02d}"


def _verificar_correspondencia(src: str, raw: bytes, rows: list[dict], prev: dict) -> None:
    """Si cayeron filas, los bytes del origen tuvieron que caer con ellas.

    **El hueco que esto tapa, y por qué el guard de merma no lo ve.** Todo lo
    demás en `source_files` sale de lo que NOSOTROS parseamos: `rows` son las
    filas insertadas, así que contrastar la tabla contra `rows` detecta un pull
    truncado —bajó una y no la otra— pero no un PARSEO truncado, donde bajan las
    dos juntas y siguen cuadrando. Ese es un falso negativo: `verificar_rfc`
    contestaría LIMPIO para contribuyentes que sí están en la lista.

    `MERMA_MAX` tampoco alcanza, porque es un umbral sobre el total: mil filas
    perdidas de 264,766 son 0.38% y pasan de largo.

    Los bytes del archivo son la única cifra que viene del origen, y de ahí su
    valor: **no se mueven si el que falla somos nosotros.** Así que en vez de una
    banda sobre bytes-por-fila, que para mover 0.38% necesitaría ser tan estrecha
    que daría falsos positivos con cualquier cambio del SAT, se compara la
    CORRESPONDENCIA de los deltas: si se perdieron N filas, el archivo tenía que
    adelgazar como N filas.

    Medido contra el 2026-11-18, donde el SAT sí quitó gente:

        Firmes          -17 filas, -1,228 B   esperaba -1,294   ratio 0.95
        No_localizados   -7 filas,   -591 B   esperaba   -582   ratio 1.02
        Cancelados       -2 filas,   -253 B   esperaba   -223   ratio 1.13

    y contra un parser que pierde filas sin que el archivo cambie, el ratio es
    0.00 sin importar si perdió mil o diez.

    **Lo que NO cubre, dicho como tal:** si el SAT achica las filas Y el parser se
    come algunas, los dos efectos se cancelan y esto no lo ve. Protege contra la
    pérdida silenciosa a formato constante, que es el caso realista.
    """
    prev_n, prev_b = prev["rows"] or 0, prev["bytes"]
    if prev_b is None:
        # Archivo importado antes de que la columna existiera. No hay línea base,
        # así que el guard EMPIEZA a proteger en la corrida siguiente, no en ésta.
        #
        # Va como WARNING y no como info a propósito: un guard que todavía no
        # vigila es justo lo que hay que saber, y en `info` se pierde entre las
        # cien líneas de descarga del cron. Aparece una vez por archivo, en la
        # primera corrida tras agregar la columna, y después nunca más: eso
        # marca con precisión cuándo empezó a proteger.
        logger.warning(
            "%s: sin bytes previos, la correspondencia NO se evalúa esta corrida; "
            "empieza a proteger en la siguiente", src,
        )
        return
    if not prev_n or not prev_b:
        return

    perdidas = prev_n - len(rows)
    if perdidas <= 0:
        return  # creció o quedó igual: no es este modo de falla

    ancho = prev_b / prev_n
    esperado = perdidas * ancho
    cayo = prev_b - len(raw)
    if cayo >= esperado * CORRESPONDENCIA_MIN:
        return

    aviso = (
        f"{src}: se perdieron {perdidas:,} filas pero el archivo sólo adelgazó "
        f"{cayo:,} bytes de los ~{esperado:,.0f} que esas filas pesaban "
        f"(ancho previo {ancho:.0f} B/fila). Si las filas desaparecieron del SAT, "
        f"el archivo pesa menos; si pesa igual, las perdimos nosotros al parsear."
    )
    # Misma salida de operador que la merma, y por la misma razón: un cambio de
    # formato del origen rompe la correspondencia de forma legítima y el código no
    # puede saberlo. Antes de encenderla, compara los RFC: un parseo roto no trae
    # registros nuevos.
    if settings.permitir_merma:
        logger.warning("%s ACEPTADO por SAT_PERMITIR_MERMA.", aviso)
        return
    raise ImportacionSospechosa(
        aviso + " Se conserva lo anterior. Si el formato del archivo cambió de "
        "verdad, corre con SAT_PERMITIR_MERMA=1."
    )


def _verificar_frescura(
    src: str, rows: list[dict], vigencia: str | None, raw: bytes | None = None
) -> None:
    """Aborta el reemplazo si el archivo nuevo es peor que el que ya está."""
    prev = db.get_source_state(src)
    if prev is None:  # primera importación: no hay nada que proteger
        return

    if raw is not None:
        _verificar_correspondencia(src, raw, rows, prev)

    prev_n = prev["rows"] or 0
    if prev_n and not rows:
        raise ImportacionSospechosa(
            f"{src}: 0 filas y había {prev_n}; se conserva lo anterior"
        )
    if prev_n and len(rows) < prev_n * (1 - MERMA_MAX):
        # La merma protege contra una descarga truncada que pisaría datos buenos.
        # Hay un caso donde el aviso es legítimo y no hay forma de que el código lo
        # sepa: una MIGRACIÓN DE FUENTE, donde lo previo venía de un archivo
        # congelado. Pasó con `Exigibles.csv` al salir de omawww: 2,747 filas contra
        # 5,535, y verificando a mano resultó que 691 RFC eran nuevos y 3,479 se
        # habían dado de baja. Una descarga truncada no trae registros nuevos.
        #
        # Así que la salida es una decisión de operador, explícita y ruidosa, no un
        # umbral más flojo: `SAT_PERMITIR_MERMA=1` para la corrida de la migración.
        if settings.permitir_merma:
            logger.warning(
                "%s: merma de %d a %d filas ACEPTADA por SAT_PERMITIR_MERMA. "
                "Si no estás migrando de fuente, apágala: tapa descargas truncadas.",
                src, prev_n, len(rows),
            )
        else:
            raise ImportacionSospechosa(
                f"{src}: {len(rows)} filas contra {prev_n} previas "
                f"(merma > {MERMA_MAX:.0%}); se conserva lo anterior. "
                "Si el SAT de verdad dio de baja a esa gente, o si cambió la fuente, "
                "corre con SAT_PERMITIR_MERMA=1; antes compara los RFC, porque una "
                "descarga truncada no trae registros nuevos y una baja real sí."
            )

    nueva, vieja = _vigencia_iso(vigencia), _vigencia_iso(prev["sat_actualizado_al"])
    if nueva and vieja and nueva < vieja:
        raise ImportacionSospechosa(
            f"{src}: el SAT declara {nueva}, anterior a {vieja} ya importada; "
            "se conserva lo anterior"
        )


def _rows_from_bytes(raw: bytes) -> list[list[str]]:
    text = raw.decode(settings.source_encoding, errors="replace")
    return list(csv.reader(io.StringIO(text)))


# ---------------------------------------------------------------------------
# Parseo (testeable con archivos locales)
# ---------------------------------------------------------------------------

def parse_69b(raw: bytes) -> tuple[list[dict], str | None]:
    """Parsea el CSV del 69-B. Devuelve (filas, vigencia_declarada)."""
    rows = _rows_from_bytes(raw)
    vigencia = None
    if rows:
        m = re.search(r"actualizada al\s+(.+?)[;\.]", rows[0][0] if rows[0] else "", re.I)
        if m:
            vigencia = m.group(1).strip()

    out: list[dict] = []
    now = _now()
    import json
    for line in rows[settings.header_row_69b:]:  # salta notas + encabezado
        if len(line) < 20 or not (line[1] or "").strip():
            continue
        out.append({
            "rfc": (line[1] or "").strip().upper(),
            "nombre": (line[2] or "").strip() or None,
            "situacion": (line[3] or "").strip() or None,
            "oficio_presuncion_sat": (line[4] or "").strip() or None,
            "publicacion_sat_presuntos": _fecha(line[5]),
            "publicacion_dof_presuntos": _fecha(line[7]),
            "publicacion_sat_desvirtuados": _fecha(line[9]),
            "publicacion_dof_desvirtuados": _fecha(line[11]),
            "oficio_definitivos_sat": (line[12] or "").strip() or None,
            "publicacion_sat_definitivos": _fecha(line[13]),
            "publicacion_dof_definitivos": _fecha(line[15]),
            "publicacion_sat_sentencia_favorable": _fecha(line[17]),
            "publicacion_dof_sentencia_favorable": _fecha(line[19]),
            "datos": json.dumps(line, ensure_ascii=False),
            "imported_at": now,
        })
    return out, vigencia


def parse_69b_bis(raw: bytes) -> tuple[list[dict], str | None]:
    """Parsea el CSV del 69-B Bis (transmisión indebida de pérdidas fiscales).

    12 columnas, 2 líneas de nota + encabezado (header_row = 3). Situaciones:
    'Definitivo' y 'Sentencia Favorable'. Devuelve (filas, vigencia_declarada).
    """
    rows = _rows_from_bytes(raw)
    vigencia = None
    if rows:
        m = re.search(r"actualizada al\s+(.+?)[;\.]?$", rows[0][0] if rows[0] else "", re.I)
        if m:
            vigencia = m.group(1).strip()

    out: list[dict] = []
    now = _now()
    import json
    for line in rows[settings.header_row_69b_bis:]:  # salta notas + encabezado
        if len(line) < 12 or not (line[1] or "").strip():
            continue
        out.append({
            "rfc": (line[1] or "").strip().upper(),
            "nombre": " ".join((line[2] or "").split()) or None,
            "situacion": (line[3] or "").strip() or None,
            "oficio_definitivo_sat": (line[4] or "").strip() or None,
            "publicacion_sat_definitivo": _fecha(line[5]),
            "oficio_definitivo_dof": (line[6] or "").strip() or None,
            "publicacion_dof_definitivo": _fecha(line[7]),
            "oficio_sentencia_favorable_sat": (line[8] or "").strip() or None,
            "publicacion_sat_sentencia_favorable": _fecha(line[9]),
            "oficio_sentencia_favorable_dof": (line[10] or "").strip() or None,
            "publicacion_dof_sentencia_favorable": _fecha(line[11]),
            "datos": json.dumps(line, ensure_ascii=False),
            "imported_at": now,
        })
    return out, vigencia


def parse_csd(raw: bytes) -> tuple[list[dict], str | None]:
    """Parsea el CSV de CSD sin efectos (Art. 17-H CFF).

    6 columnas, encabezado en línea 1 (header_row = 1): RFC, nombre, supuesto de
    cancelación, fecha de cancelación, administración responsable, fecha de
    publicación. No trae nota de vigencia → (filas, None).
    """
    rows = _rows_from_bytes(raw)
    out: list[dict] = []
    now = _now()
    import json
    for line in rows[settings.header_row_csd:]:  # salta encabezado
        if len(line) < 6 or not (line[0] or "").strip():
            continue
        out.append({
            "rfc": (line[0] or "").strip().upper(),
            "nombre": " ".join((line[1] or "").split()) or None,
            "supuesto": (line[2] or "").strip() or None,
            "fecha_cancelacion": _fecha(line[3]),
            "admon_responsable": (line[4] or "").strip() or None,
            "fecha_publicacion": _fecha(line[5]),
            "datos": json.dumps(line, ensure_ascii=False),
            "imported_at": now,
        })
    return out, None


def parse_69(raw: bytes, source_file: str) -> list[dict]:
    """Parsea un CSV del 69 (6 columnas, encabezado en línea 1)."""
    rows = _rows_from_bytes(raw)
    out: list[dict] = []
    now = _now()
    for line in rows[1:]:  # salta encabezado
        if len(line) < 6 or not (line[0] or "").strip():
            continue
        out.append({
            "rfc": (line[0] or "").strip().upper(),
            "razon_social": (line[1] or "").strip() or None,
            "tipo_persona": (line[2] or "").strip() or None,
            "supuesto": (line[3] or "").strip() or None,
            "fecha_primera_publicacion": _fecha(line[4]),
            "entidad_federativa": (line[5] or "").strip() or None,
            "source_file": source_file,
            "imported_at": now,
        })
    return out


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def _import_69b(force: bool) -> dict:
    raw, modificado = fetch_csv(settings.url_69b)
    h = sha256(raw)
    src = "Listado_Completo_69-B.csv"
    if not force and db.get_source_hash(src) == h:
        return {"source_file": src, "cached": True, "rows": None}

    rows, vigencia = parse_69b(raw)
    vigencia = vigencia or modificado
    _verificar_frescura(src, rows, vigencia, raw=raw)
    n = db.replace_69b(rows)
    db.record_source("69b", src, h, n, _now(), sat_actualizado_al=vigencia,
                     bytes_origen=len(raw))
    return {"source_file": src, "cached": False, "rows": n, "sat_actualizado_al": vigencia}


def _import_69b_bis(force: bool) -> dict:
    src = "Listado_69_B_Bis_Completo.csv"
    if not settings.url_69b_bis:  # feature-flag: sin URL configurada, se omite
        return {"source_file": src, "skipped": "SAT_URL_69B_BIS no configurada"}
    raw, modificado = fetch_csv(settings.url_69b_bis)
    h = sha256(raw)
    if not force and db.get_source_hash(src) == h:
        return {"source_file": src, "cached": True, "rows": None}

    rows, vigencia = parse_69b_bis(raw)
    vigencia = vigencia or modificado
    _verificar_frescura(src, rows, vigencia, raw=raw)
    n = db.replace_69b_bis(rows)
    db.record_source("69bbis", src, h, n, _now(), sat_actualizado_al=vigencia,
                     bytes_origen=len(raw))
    return {"source_file": src, "cached": False, "rows": n, "sat_actualizado_al": vigencia}


def _import_csd(force: bool) -> dict:
    src = "CSDsinefectos.csv"
    raw, modificado = fetch_csv(settings.url_csd)
    h = sha256(raw)
    if not force and db.get_source_hash(src) == h:
        return {"source_file": src, "cached": True, "rows": None}

    rows, vigencia = parse_csd(raw)
    vigencia = vigencia or modificado  # el CSD no trae nota: su vigencia es el Last-Modified
    _verificar_frescura(src, rows, vigencia, raw=raw)
    n = db.replace_csd(rows)
    db.record_source("csd", src, h, n, _now(), sat_actualizado_al=vigencia,
                     bytes_origen=len(raw))
    return {"source_file": src, "cached": False, "rows": n, "sat_actualizado_al": vigencia}


def _import_69_file(fname: str, force: bool) -> dict:
    url = settings.base_69.rstrip("/") + "/" + fname
    raw, modificado = fetch_csv(url)
    h = sha256(raw)
    if not force and db.get_source_hash(fname) == h:
        return {"source_file": fname, "cached": True, "rows": None}

    rows = parse_69(raw, fname)
    _verificar_frescura(fname, rows, modificado, raw=raw)
    n = db.replace_69_file(fname, rows)
    db.record_source("69", fname, h, n, _now(), sat_actualizado_al=modificado,
                     bytes_origen=len(raw))
    return {"source_file": fname, "cached": False, "rows": n, "sat_actualizado_al": modificado}


def process_import(dataset: str = "all", force_refresh: bool = False) -> dict:
    """Descarga y sincroniza los listados del SAT.

    dataset: 'all' | '69' | '69b' | '69bbis' | 'csd'
    Idempotente por hash de archivo (salvo force_refresh).
    """
    dataset = (dataset or "all").lower().replace("69b_bis", "69bbis")
    resultados: list[dict] = []
    errores: list[dict] = []

    try:
        if dataset in ("all", "69b"):
            try:
                resultados.append(_import_69b(force_refresh))
            except Exception as exc:  # noqa: BLE001
                logger.error("69-B falló: %s", exc)
                errores.append({"source_file": "Listado_Completo_69-B.csv", "error": str(exc)})

        if dataset in ("all", "69bbis"):
            try:
                resultados.append(_import_69b_bis(force_refresh))
            except Exception as exc:  # noqa: BLE001
                logger.error("69-B Bis falló: %s", exc)
                errores.append({"source_file": "Listado_69_B_Bis_Completo.csv", "error": str(exc)})

        if dataset in ("all", "csd"):
            try:
                resultados.append(_import_csd(force_refresh))
            except Exception as exc:  # noqa: BLE001
                logger.error("CSD sin efectos falló: %s", exc)
                errores.append({"source_file": "CSDsinefectos.csv", "error": str(exc)})

        if dataset in ("all", "69"):
            for fname in settings.files_69:
                try:
                    resultados.append(_import_69_file(fname, force_refresh))
                except Exception as exc:  # noqa: BLE001
                    logger.error("69 [%s] falló: %s", fname, exc)
                    errores.append({"source_file": fname, "error": str(exc)})

            # Se corre DESPUÉS de importar y sólo si no hubo errores: purgar
            # cuando una descarga falló dejaría el 69 a medias y sin la lista
            # vieja para responder mientras tanto.
            if not errores:
                for r in db.purgar_archivos_69_retirados(settings.files_69):
                    logger.warning(
                        "69 [%s]: %d filas de un archivo que ya no se ingiere, purgadas",
                        r["source_file"], r["filas"],
                    )
                    resultados.append({"source_file": r["source_file"], "purgado": r["filas"]})

        # Persistencia durable. Sólo si algo cambió: con el pull de `main()` puesto,
        # una corrida donde todo salió de la caché deja el local igual al remoto, y
        # pushear 580k filas para reescribirlas idénticas es gasto y una ventana más
        # de pull parcial para quien arranque en ese momento.
        cambio = any(not r.get("cached") for r in resultados)
        # **Un push fallido NO se confunde con "no había nada que empujar".**
        #
        # Los dos casos dejaban `turso_rows_pushed` en 0, y desde que el workflow
        # decide con ese número (sat69-mcp#28) eso tenía una consecuencia fea: un
        # push roto imprimía "el SAT no publicó nada nuevo" y la corrida pasaba
        # en VERDE, todos los días, mientras el servicio seguía sirviendo el
        # corte viejo. El almacén durable no recibía lo que la ingesta acababa de
        # importar y nadie se enteraba.
        #
        # La inferencia es segura aquí: `cambio` sólo es cierto si se importó
        # algo, así que si hubo cambios y el push devolvió 0, falló. `push_to_turso`
        # devuelve 0 en vez de levantar, y eso se respeta: lo que cambia es que
        # ahora el pipeline lo cuenta como error, con lo cual `main()` sale con 1
        # y el paso del workflow truena antes de llegar al aviso al servicio.
        pushed = 0
        if not cambio:
            logger.info("Nada cambió — se omite el push a Turso")
        else:
            try:
                from .turso import push_to_turso
                pushed = push_to_turso()
            except Exception as exc:  # noqa: BLE001
                logger.error("Push a Turso falló: %s", exc)
            if pushed == 0:
                logger.error(
                    "Se importaron cambios y el push a Turso no envió nada: el "
                    "almacén durable NO tiene lo que se acaba de importar."
                )
                errores.append({
                    "source_file": "(push a Turso)",
                    "error": "el push no envió filas habiendo cambios importados",
                })

        total = sum(r.get("rows") or 0 for r in resultados if not r.get("cached"))
        return {
            "success": not errores,
            "dataset": dataset,
            "rows_importados": total,
            "resultados": resultados,
            "errores": errores,
            "turso_rows_pushed": pushed,
            "message": f"{total} registros importados; {len(errores)} error(es).",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("process_import falló")
        return {"success": False, "error": str(exc)}


def _traer_estado_previo() -> bool:
    """Pull desde Turso antes de importar. Devuelve si hay que forzar la ingesta.

    El cron arranca con un `/tmp/sat69.db` vacío cada corrida, y eso dejaba sin
    piso a las dos cosas que dependen del estado previo:

    - **`_verificar_frescura`** toma el camino de "primera importación: no hay nada
      que proteger", así que ninguno de sus tres guards corre nunca en producción.
      El congelamiento de omawww habría pasado desapercibido incluso con el guard
      del `Last-Modified` puesto.
    - **La idempotencia por hash** no puede acertar, porque `get_source_hash`
      siempre devuelve None. Cada corrida reimportaba y reescribía 580k filas a
      Turso aunque el SAT no hubiera publicado nada.

    Dos cosas que este pull NO puede hacer mal, y por eso las decide aquí:

    1. **Si Turso está configurado y el pull truena, no se importa.** Importar
       sobre una base vacía y luego pushear reemplazaría el remoto con una ingesta
       que ningún guard revisó. Mejor no correr que correr a ciegas.
    2. **Si lo que llegó está a medias, se fuerza la ingesta completa.** Con un
       pull parcial, la caché por hash salta archivos cuyas filas locales están
       incompletas, y el push de después escribiría ese pedazo a Turso. O sea que
       la caché sólo se puede creer si el piso está completo.
    """
    from .turso import pull_from_turso

    if not settings.turso_enabled:
        logger.info("Turso no configurado — se importa sin estado previo")
        return True

    filas = pull_from_turso()  # PullFallido sube y aborta la corrida
    if not filas:
        logger.info("Turso vacío — se importa todo")
        return True

    incompletas = db.listas_incompletas()
    if incompletas:
        logger.warning(
            "El pull trajo %d filas pero el índice quedó a medias (%s): se fuerza la "
            "ingesta completa, porque la caché por hash saltaría archivos parciales "
            "y el push los mandaría así a Turso.",
            filas, ", ".join(incompletas),
        )
        return True

    logger.info("Estado previo traído de Turso: %d filas. Los guards tienen piso.", filas)
    return False


def main() -> int:
    """CLI: `python -m sat69.pipeline [--force]` — pull, ingesta y push a Turso.

    Pensado para el cron de GitHub Actions (runner fiable, sin spin-down del free
    tier de Render). Render sólo hace pull en el arranque.
    """
    import pathlib
    import sys

    from .turso import PullFallido

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    db.init_db(pathlib.Path(settings.db_path))

    try:
        forzar = _traer_estado_previo()
    except PullFallido as exc:
        logger.error("No se importa nada: %s", exc)
        return 1

    result = process_import("all", force_refresh=forzar or "--force" in sys.argv)
    logger.info(
        "Resultado: %s (push Turso: %s)", result.get("message"), result.get("turso_rows_pushed")
    )
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
