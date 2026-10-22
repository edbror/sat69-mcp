"""
Capa de base de datos: SQLite + FTS5.

Tablas reales:
  • registros_69   — Art. 69 (situación fiscal firme). Clave de reimport: source_file.
  • registros_69b  — Art. 69-B (EFOS / operaciones simuladas).
  • source_files   — control de ingesta por archivo (hash, filas, vigencia) → idempotencia.

Índices FTS5 (unicode61, sin acentos) para búsqueda por nombre/razón social:
  • reg69_fts, reg69b_fts — mantenidos con triggers (igual patrón que pages_fts
    del MCP de movilizaciones).

El acceso por RFC va por índice B-tree (búsqueda exacta, el camino caliente).
"""
from __future__ import annotations

import sqlite3
import unicodedata
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from . import risk


def unaccent(text: str | None) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


_DB_PATH: Path | None = None


def configure(db_path: Path) -> None:
    global _DB_PATH
    _DB_PATH = db_path


def _path() -> Path:
    if _DB_PATH is None:
        raise RuntimeError("Base de datos no configurada. Llama init_db() primero.")
    return _DB_PATH


def is_configured() -> bool:
    return _DB_PATH is not None


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.create_function("unaccent", 1, unaccent, deterministic=True)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS registros_69 (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    rfc                       TEXT NOT NULL,
    razon_social              TEXT,
    tipo_persona              TEXT,
    supuesto                  TEXT,
    fecha_primera_publicacion TEXT,
    entidad_federativa        TEXT,
    source_file               TEXT NOT NULL,
    imported_at               TEXT
);
CREATE INDEX IF NOT EXISTS idx_69_rfc    ON registros_69(rfc);
CREATE INDEX IF NOT EXISTS idx_69_src    ON registros_69(source_file);
CREATE INDEX IF NOT EXISTS idx_69_sup    ON registros_69(supuesto);

CREATE TABLE IF NOT EXISTS registros_69b (
    id                                   INTEGER PRIMARY KEY AUTOINCREMENT,
    rfc                                  TEXT NOT NULL,
    nombre                               TEXT,
    situacion                            TEXT,
    oficio_presuncion_sat                TEXT,
    publicacion_sat_presuntos            TEXT,
    publicacion_dof_presuntos            TEXT,
    publicacion_sat_desvirtuados         TEXT,
    publicacion_dof_desvirtuados         TEXT,
    oficio_definitivos_sat               TEXT,
    publicacion_sat_definitivos          TEXT,
    publicacion_dof_definitivos          TEXT,
    publicacion_sat_sentencia_favorable  TEXT,
    publicacion_dof_sentencia_favorable  TEXT,
    datos                                TEXT,
    imported_at                          TEXT
);
CREATE INDEX IF NOT EXISTS idx_69b_rfc ON registros_69b(rfc);
CREATE INDEX IF NOT EXISTS idx_69b_sit ON registros_69b(situacion);

CREATE TABLE IF NOT EXISTS registros_69b_bis (
    id                                   INTEGER PRIMARY KEY AUTOINCREMENT,
    rfc                                  TEXT NOT NULL,
    nombre                               TEXT,
    situacion                            TEXT,
    oficio_definitivo_sat                TEXT,
    publicacion_sat_definitivo           TEXT,
    oficio_definitivo_dof                TEXT,
    publicacion_dof_definitivo           TEXT,
    oficio_sentencia_favorable_sat       TEXT,
    publicacion_sat_sentencia_favorable  TEXT,
    oficio_sentencia_favorable_dof       TEXT,
    publicacion_dof_sentencia_favorable  TEXT,
    datos                                TEXT,
    imported_at                          TEXT
);
CREATE INDEX IF NOT EXISTS idx_69bbis_rfc ON registros_69b_bis(rfc);
CREATE INDEX IF NOT EXISTS idx_69bbis_sit ON registros_69b_bis(situacion);

CREATE TABLE IF NOT EXISTS registros_csd (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    rfc                TEXT NOT NULL,
    nombre             TEXT,
    supuesto           TEXT,
    fecha_cancelacion  TEXT,
    admon_responsable  TEXT,
    fecha_publicacion  TEXT,
    datos              TEXT,
    imported_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_csd_rfc ON registros_csd(rfc);
CREATE INDEX IF NOT EXISTS idx_csd_sup ON registros_csd(supuesto);

CREATE TABLE IF NOT EXISTS source_files (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset            TEXT NOT NULL,        -- '69' | '69b'
    source_file        TEXT NOT NULL UNIQUE,
    sha256             TEXT,
    rows               INTEGER DEFAULT 0,
    sat_actualizado_al TEXT,
    status             TEXT DEFAULT 'ok',
    error_msg          TEXT,
    fetched_at         TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS reg69_fts USING fts5(
    rfc UNINDEXED,
    nombre,
    tokenize = 'unicode61 remove_diacritics 1'
);
CREATE VIRTUAL TABLE IF NOT EXISTS reg69b_fts USING fts5(
    rfc UNINDEXED,
    nombre,
    tokenize = 'unicode61 remove_diacritics 1'
);
CREATE VIRTUAL TABLE IF NOT EXISTS reg69bbis_fts USING fts5(
    rfc UNINDEXED,
    nombre,
    tokenize = 'unicode61 remove_diacritics 1'
);
CREATE VIRTUAL TABLE IF NOT EXISTS reg_csd_fts USING fts5(
    rfc UNINDEXED,
    nombre,
    tokenize = 'unicode61 remove_diacritics 1'
);

CREATE TRIGGER IF NOT EXISTS reg69_ai AFTER INSERT ON registros_69
WHEN NEW.razon_social IS NOT NULL BEGIN
    INSERT INTO reg69_fts(rowid, rfc, nombre) VALUES (NEW.id, NEW.rfc, NEW.razon_social);
END;
CREATE TRIGGER IF NOT EXISTS reg69_ad AFTER DELETE ON registros_69 BEGIN
    DELETE FROM reg69_fts WHERE rowid = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS reg69b_ai AFTER INSERT ON registros_69b
WHEN NEW.nombre IS NOT NULL BEGIN
    INSERT INTO reg69b_fts(rowid, rfc, nombre) VALUES (NEW.id, NEW.rfc, NEW.nombre);
END;
CREATE TRIGGER IF NOT EXISTS reg69b_ad AFTER DELETE ON registros_69b BEGIN
    DELETE FROM reg69b_fts WHERE rowid = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS reg69bbis_ai AFTER INSERT ON registros_69b_bis
WHEN NEW.nombre IS NOT NULL BEGIN
    INSERT INTO reg69bbis_fts(rowid, rfc, nombre) VALUES (NEW.id, NEW.rfc, NEW.nombre);
END;
CREATE TRIGGER IF NOT EXISTS reg69bbis_ad AFTER DELETE ON registros_69b_bis BEGIN
    DELETE FROM reg69bbis_fts WHERE rowid = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS reg_csd_ai AFTER INSERT ON registros_csd
WHEN NEW.nombre IS NOT NULL BEGIN
    INSERT INTO reg_csd_fts(rowid, rfc, nombre) VALUES (NEW.id, NEW.rfc, NEW.nombre);
END;
CREATE TRIGGER IF NOT EXISTS reg_csd_ad AFTER DELETE ON registros_csd BEGIN
    DELETE FROM reg_csd_fts WHERE rowid = OLD.id;
END;
"""


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    configure(db_path)
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# Carga (usada por el pipeline)
# ---------------------------------------------------------------------------

def replace_69_file(source_file: str, rows: list[dict]) -> int:
    """Reemplaza los registros del Art. 69 de un archivo dado (idempotente)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM registros_69 WHERE source_file = ?", (source_file,))
        conn.executemany(
            """
            INSERT INTO registros_69
                (rfc, razon_social, tipo_persona, supuesto,
                 fecha_primera_publicacion, entidad_federativa, source_file, imported_at)
            VALUES (:rfc, :razon_social, :tipo_persona, :supuesto,
                    :fecha_primera_publicacion, :entidad_federativa, :source_file, :imported_at)
            """,
            rows,
        )
    return len(rows)


def replace_69b(rows: list[dict]) -> int:
    """Reemplaza por completo el Art. 69-B (snapshot único del SAT)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM registros_69b")
        conn.executemany(
            """
            INSERT INTO registros_69b (
                rfc, nombre, situacion, oficio_presuncion_sat,
                publicacion_sat_presuntos, publicacion_dof_presuntos,
                publicacion_sat_desvirtuados, publicacion_dof_desvirtuados,
                oficio_definitivos_sat, publicacion_sat_definitivos,
                publicacion_dof_definitivos, publicacion_sat_sentencia_favorable,
                publicacion_dof_sentencia_favorable, datos, imported_at
            ) VALUES (
                :rfc, :nombre, :situacion, :oficio_presuncion_sat,
                :publicacion_sat_presuntos, :publicacion_dof_presuntos,
                :publicacion_sat_desvirtuados, :publicacion_dof_desvirtuados,
                :oficio_definitivos_sat, :publicacion_sat_definitivos,
                :publicacion_dof_definitivos, :publicacion_sat_sentencia_favorable,
                :publicacion_dof_sentencia_favorable, :datos, :imported_at
            )
            """,
            rows,
        )
    return len(rows)


def replace_69b_bis(rows: list[dict]) -> int:
    """Reemplaza por completo el Art. 69-B Bis (snapshot único del SAT)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM registros_69b_bis")
        conn.executemany(
            """
            INSERT INTO registros_69b_bis (
                rfc, nombre, situacion,
                oficio_definitivo_sat, publicacion_sat_definitivo,
                oficio_definitivo_dof, publicacion_dof_definitivo,
                oficio_sentencia_favorable_sat, publicacion_sat_sentencia_favorable,
                oficio_sentencia_favorable_dof, publicacion_dof_sentencia_favorable,
                datos, imported_at
            ) VALUES (
                :rfc, :nombre, :situacion,
                :oficio_definitivo_sat, :publicacion_sat_definitivo,
                :oficio_definitivo_dof, :publicacion_dof_definitivo,
                :oficio_sentencia_favorable_sat, :publicacion_sat_sentencia_favorable,
                :oficio_sentencia_favorable_dof, :publicacion_dof_sentencia_favorable,
                :datos, :imported_at
            )
            """,
            rows,
        )
    return len(rows)


def replace_csd(rows: list[dict]) -> int:
    """Reemplaza por completo la lista de CSD sin efectos (snapshot único del SAT)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM registros_csd")
        conn.executemany(
            """
            INSERT INTO registros_csd (
                rfc, nombre, supuesto, fecha_cancelacion,
                admon_responsable, fecha_publicacion, datos, imported_at
            ) VALUES (
                :rfc, :nombre, :supuesto, :fecha_cancelacion,
                :admon_responsable, :fecha_publicacion, :datos, :imported_at
            )
            """,
            rows,
        )
    return len(rows)


def purgar_archivos_69_retirados(vigentes: tuple[str, ...]) -> list[dict]:
    """Borra los registros del 69 cuyo archivo de origen ya no se ingiere.

    `replace_69_file` reemplaza POR archivo, así que renombrar o retirar una fuente
    deja sus filas vivas para siempre. Pasó al migrar a la fuente del blob:
    `NoLocalizados.csv` se volvió `No_localizados.csv` y quedaron las dos, con el
    total del 69 contando doble. Peor que el conteo: `verificar_rfc` no filtra por
    archivo, así que un RFC que el SAT ya dio de baja seguía apareciendo desde el
    archivo viejo. Un falso positivo sobre un contribuyente, por dato zombi.

    Es caché de listas públicas del SAT, no traza: se vuelve a descargar entera.
    """
    marcas = ",".join("?" * len(vigentes)) or "''"
    with get_conn() as conn:
        retirados = [
            dict(r) for r in conn.execute(
                f"""
                SELECT source_file, COUNT(*) AS filas FROM registros_69
                WHERE source_file NOT IN ({marcas})
                GROUP BY source_file
                """,
                vigentes,
            ).fetchall()
        ]
        if retirados:
            conn.execute(
                f"DELETE FROM registros_69 WHERE source_file NOT IN ({marcas})", vigentes
            )
            conn.execute(
                f"DELETE FROM source_files WHERE dataset = '69' "
                f"AND source_file NOT IN ({marcas})",
                vigentes,
            )
    return retirados


def record_source(
    dataset: str, source_file: str, sha256: str, rows: int,
    fetched_at: str, sat_actualizado_al: str | None = None,
    status: str = "ok", error_msg: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO source_files
                (dataset, source_file, sha256, rows, sat_actualizado_al,
                 status, error_msg, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_file) DO UPDATE SET
                sha256             = excluded.sha256,
                rows               = excluded.rows,
                sat_actualizado_al = excluded.sat_actualizado_al,
                status             = excluded.status,
                error_msg          = excluded.error_msg,
                fetched_at         = excluded.fetched_at
            """,
            (dataset, source_file, sha256, rows, sat_actualizado_al,
             status, error_msg, fetched_at),
        )


def get_source_hash(source_file: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT sha256 FROM source_files WHERE source_file = ?", (source_file,)
        ).fetchone()
        return row["sha256"] if row else None


def get_source_state(source_file: str) -> dict | None:
    """Lo que dejó la última importación buena: filas y vigencia declarada.

    `None` si el archivo nunca se importó — ahí no hay nada que proteger y el
    guard de frescura de `pipeline` deja pasar la primera carga.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT rows, sat_actualizado_al FROM source_files WHERE source_file = ?",
            (source_file,),
        ).fetchone()
        if row is None:
            return None
        return {"rows": row["rows"], "sat_actualizado_al": row["sat_actualizado_al"]}


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------

_LISTAS = {
    "Art. 69": ("registros_69", "69"),
    "Art. 69-B": ("registros_69b", "69b"),
    "Art. 69-B Bis": ("registros_69b_bis", "69bbis"),
    "CSD sin efectos": ("registros_csd", "csd"),
}

# Cuánto puede faltarle a una tabla respecto de lo que `source_files` declara antes
# de considerarla incompleta. No es cero porque el parser descarta filas sin RFC y
# `source_files` guarda las filas insertadas, así que coinciden; el margen es para
# no volverse quisquilloso con una diferencia de redondeo.
_FALTANTE_MAX = 0.02


def _listas_incompletas() -> list[str]:
    """Las listas que no se pueden declarar limpias: vacías o a medias.

    Vacía es el caso obvio. **A medias es el que de verdad muerde**, porque no se
    ve: `push_to_turso` hace DELETE y luego INSERT por lotes, así que un `pull` que
    cae en medio se trae un pedazo, y el servicio lo sirve como si estuviera
    completo. Pasó dos veces en producción: el 18-oct importó 15,024 de 557,094
    filas y el 22-oct 75,847, y las dos veces `verificar_rfc` contestó LIMPIO para
    todo lo que faltaba, un día entero, sin que nada avisara.

    El contraste está a mano: `source_files` dice cuántas filas trajo cada archivo,
    y se replica junto con los datos. Si la tabla tiene mucho menos que eso, el
    índice está a medias.
    """
    faltan = []
    with get_conn() as conn:
        esperado: dict[str, int] = {}
        for ds, n in conn.execute(
            "SELECT dataset, COALESCE(SUM(rows), 0) FROM source_files "
            "WHERE status = 'ok' GROUP BY dataset"
        ):
            esperado[ds] = n or 0

        for etiqueta, (tabla, dataset) in _LISTAS.items():
            real = conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
            if not real:
                faltan.append(etiqueta)
                continue
            n = esperado.get(dataset, 0)
            if n and real < n * (1 - _FALTANTE_MAX):
                faltan.append(f"{etiqueta} ({real:,} de {n:,} filas)")
    return faltan


def verificar_rfc(rfc_original: str) -> dict:
    """Verifica un RFC contra ambas listas y devuelve el veredicto de riesgo."""
    rfc = risk.normalizar_rfc(rfc_original)
    with get_conn() as conn:
        r69 = [dict(r) for r in conn.execute(
            """
            SELECT supuesto, razon_social, tipo_persona, entidad_federativa,
                   fecha_primera_publicacion
            FROM registros_69 WHERE rfc = ?
            """, (rfc,)).fetchall()]
        r69b = [dict(r) for r in conn.execute(
            """
            SELECT nombre, situacion, publicacion_dof_presuntos,
                   publicacion_dof_definitivos, publicacion_dof_sentencia_favorable
            FROM registros_69b WHERE rfc = ?
            """, (rfc,)).fetchall()]
        r69bbis = [dict(r) for r in conn.execute(
            """
            SELECT nombre, situacion, publicacion_dof_definitivo,
                   publicacion_dof_sentencia_favorable
            FROM registros_69b_bis WHERE rfc = ?
            """, (rfc,)).fetchall()]
        rcsd = [dict(r) for r in conn.execute(
            """
            SELECT nombre, supuesto, fecha_cancelacion, admon_responsable,
                   fecha_publicacion
            FROM registros_csd WHERE rfc = ?
            """, (rfc,)).fetchall()]

    riesgo, veredicto = risk.evaluar(
        [x["supuesto"] for x in r69],
        [x["situacion"] for x in r69b],
        [x["situacion"] for x in r69bbis],
        [x["supuesto"] for x in rcsd],
    )

    # Un veredicto no puede absolver a una lista que no se pudo consultar. Si una
    # tabla está vacía —arranque en frío, pull de Turso a medias, ingesta que
    # tronó— `evaluar` no encuentra nada en ella y devuelve LIMPIO con un texto
    # que afirma haber revisado las cuatro. Eso es una constancia falsa, y es
    # exactamente lo que pasó: con la tabla del CSD en cero, un RFC con el sello
    # cancelado desde 2024 salía LIMPIO.
    #
    # Sólo degrada el LIMPIO: un hallazgo en otra lista sigue siendo un hallazgo,
    # y ahí el veredicto ya dice algo verdadero.
    faltantes = _listas_incompletas()
    if faltantes and riesgo == "LIMPIO":
        riesgo = "NO_VERIFICADO"
        veredicto = (
            "No se pudo emitir veredicto: "
            f"{', '.join(faltantes)} sin datos o a medias en el índice. "
            "Registra 'no verificado', nunca 'limpio'."
        )

    return {
        "rfc_consultado": rfc_original,
        "rfc": rfc,
        "rfc_valido": risk.rfc_valido(rfc),
        "riesgo": riesgo,
        "veredicto": veredicto,
        "listas_sin_datos": faltantes,
        "en_69b": bool(r69b),
        "en_69b_bis": bool(r69bbis),
        "en_csd": bool(rcsd),
        "en_69": bool(r69),
        "registros_69b": r69b,
        "registros_69b_bis": r69bbis,
        "registros_csd": rcsd,
        "registros_69": r69,
    }


def buscar_nombre(texto: str, dataset: str = "ambos", limite: int = 25) -> dict:
    texto = (texto or "").strip()
    limite = max(1, min(limite, 100))
    # FTS5: usamos prefijo para tolerar coincidencias parciales.
    match = " ".join(f'"{t}"*' for t in unaccent(texto).split())
    out: dict = {}
    with get_conn() as conn:
        if dataset in ("ambos", "69b"):
            out["69b"] = [dict(r) for r in conn.execute(
                """
                SELECT r.rfc, r.nombre, r.situacion
                FROM reg69b_fts f JOIN registros_69b r ON f.rowid = r.id
                WHERE reg69b_fts MATCH ? LIMIT ?
                """, (match, limite)).fetchall()]
        if dataset in ("ambos", "69bbis"):
            out["69b_bis"] = [dict(r) for r in conn.execute(
                """
                SELECT r.rfc, r.nombre, r.situacion
                FROM reg69bbis_fts f JOIN registros_69b_bis r ON f.rowid = r.id
                WHERE reg69bbis_fts MATCH ? LIMIT ?
                """, (match, limite)).fetchall()]
        if dataset in ("ambos", "csd"):
            out["csd"] = [dict(r) for r in conn.execute(
                """
                SELECT r.rfc, r.nombre, r.supuesto, r.fecha_cancelacion
                FROM reg_csd_fts f JOIN registros_csd r ON f.rowid = r.id
                WHERE reg_csd_fts MATCH ? LIMIT ?
                """, (match, limite)).fetchall()]
        if dataset in ("ambos", "69"):
            out["69"] = [dict(r) for r in conn.execute(
                """
                SELECT r.rfc, r.razon_social, r.supuesto, r.entidad_federativa
                FROM reg69_fts f JOIN registros_69 r ON f.rowid = r.id
                WHERE reg69_fts MATCH ? LIMIT ?
                """, (match, limite)).fetchall()]
    return out


def estado_datos() -> dict:
    with get_conn() as conn:
        total_69 = conn.execute("SELECT COUNT(*) c FROM registros_69").fetchone()["c"]
        total_69b = conn.execute("SELECT COUNT(*) c FROM registros_69b").fetchone()["c"]
        total_69bbis = conn.execute("SELECT COUNT(*) c FROM registros_69b_bis").fetchone()["c"]
        total_csd = conn.execute("SELECT COUNT(*) c FROM registros_csd").fetchone()["c"]
        por_supuesto = {r["supuesto"]: r["n"] for r in conn.execute(
            "SELECT supuesto, COUNT(*) n FROM registros_69 GROUP BY supuesto").fetchall()}
        por_situacion = {r["situacion"]: r["n"] for r in conn.execute(
            "SELECT situacion, COUNT(*) n FROM registros_69b GROUP BY situacion").fetchall()}
        por_situacion_bis = {r["situacion"]: r["n"] for r in conn.execute(
            "SELECT situacion, COUNT(*) n FROM registros_69b_bis GROUP BY situacion").fetchall()}
        por_supuesto_csd = {r["supuesto"]: r["n"] for r in conn.execute(
            "SELECT supuesto, COUNT(*) n FROM registros_csd GROUP BY supuesto").fetchall()}
        fuentes = [dict(r) for r in conn.execute(
            "SELECT dataset, source_file, rows, sat_actualizado_al, status, fetched_at "
            "FROM source_files ORDER BY dataset, source_file").fetchall()]

    if total_69 == 0 and total_69b == 0 and total_69bbis == 0 and total_csd == 0:
        return {"status": "no_data",
                "message": "No hay datos. Ejecuta actualizar_datos() primero."}

    vig = next((f["sat_actualizado_al"] for f in fuentes
                if f["dataset"] == "69b" and f["sat_actualizado_al"]), None)
    vig_bis = next((f["sat_actualizado_al"] for f in fuentes
                    if f["dataset"] == "69bbis" and f["sat_actualizado_al"]), None)
    ult = max((f["fetched_at"] for f in fuentes if f["fetched_at"]), default=None)
    return {
        "status": "ok",
        "art_69": {"total_registros": total_69, "por_supuesto": por_supuesto},
        "art_69b": {"total_registros": total_69b, "por_situacion": por_situacion,
                    "sat_actualizado_al": vig},
        "art_69b_bis": {"total_registros": total_69bbis,
                        "por_situacion": por_situacion_bis,
                        "sat_actualizado_al": vig_bis},
        "csd_sin_efectos": {"total_registros": total_csd,
                            "por_supuesto": por_supuesto_csd},
        "ultima_importacion": ult,
        "fuentes": fuentes,
    }
