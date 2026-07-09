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
    raw = fetch_csv(settings.url_69b)
    h = sha256(raw)
    src = "Listado_Completo_69-B.csv"
    if not force and db.get_source_hash(src) == h:
        return {"source_file": src, "cached": True, "rows": None}

    rows, vigencia = parse_69b(raw)
    n = db.replace_69b(rows)
    db.record_source("69b", src, h, n, _now(), sat_actualizado_al=vigencia)
    return {"source_file": src, "cached": False, "rows": n, "sat_actualizado_al": vigencia}


def _import_69_file(fname: str, force: bool) -> dict:
    url = settings.base_69.rstrip("/") + "/" + fname
    raw = fetch_csv(url)
    h = sha256(raw)
    if not force and db.get_source_hash(fname) == h:
        return {"source_file": fname, "cached": True, "rows": None}

    rows = parse_69(raw, fname)
    n = db.replace_69_file(fname, rows)
    db.record_source("69", fname, h, n, _now())
    return {"source_file": fname, "cached": False, "rows": n}


def process_import(dataset: str = "all", force_refresh: bool = False) -> dict:
    """Descarga y sincroniza los listados del SAT.

    dataset: 'all' | '69' | '69b'
    Idempotente por hash de archivo (salvo force_refresh).
    """
    dataset = (dataset or "all").lower()
    resultados: list[dict] = []
    errores: list[dict] = []

    try:
        if dataset in ("all", "69b"):
            try:
                resultados.append(_import_69b(force_refresh))
            except Exception as exc:  # noqa: BLE001
                logger.error("69-B falló: %s", exc)
                errores.append({"source_file": "Listado_Completo_69-B.csv", "error": str(exc)})

        if dataset in ("all", "69"):
            for fname in settings.files_69:
                try:
                    resultados.append(_import_69_file(fname, force_refresh))
                except Exception as exc:  # noqa: BLE001
                    logger.error("69 [%s] falló: %s", fname, exc)
                    errores.append({"source_file": fname, "error": str(exc)})

        # Persistencia durable.
        pushed = 0
        try:
            from .turso import push_to_turso
            pushed = push_to_turso()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Push a Turso falló (no fatal): %s", exc)

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


def main() -> int:
    """CLI: `python -m sat69.pipeline [--force]` — ingesta completa + push a Turso.

    Pensado para el cron de GitHub Actions (runner fiable, sin spin-down del free
    tier de Render). Render sólo hace pull en el arranque.
    """
    import pathlib
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    db.init_db(pathlib.Path(settings.db_path))
    result = process_import("all", force_refresh="--force" in sys.argv)
    logger.info(
        "Resultado: %s (push Turso: %s)", result.get("message"), result.get("turso_rows_pushed")
    )
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
