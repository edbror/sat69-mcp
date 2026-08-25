"""
Configuración centralizada del servidor SAT 69 / 69-B.

Toda la lectura de variables de entorno vive aquí para que el resto del
paquete dependa de un único objeto `settings`.

Espejo de la arquitectura del MCP de movilizaciones (SSC CDMX): mismo patrón de
proxy de descarga (FETCH_PROXY / DataImpulse), OAuth (WorkOS AuthKit), bearer
estático y Turso.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_ROOT.parents[1]

# --- Fuentes de datos abiertos del SAT ------------------------------------
# 69-B (EFOS): un archivo, 20 columnas, 2 líneas de nota antes del encabezado
# real (por eso header_row = 3, basado en 1).
# 2026-07: el SAT migró el archivo de datos abiertos 69-B a un blob de Azure; la
# ruta vieja de omawww quedó CONGELADA en el corte 31-dic-2025 (seguía dando 200,
# por eso el cron no fallaba mientras re-importaba datos viejos). Azure trae el
# corte vigente. Overrideable por env por si el SAT vuelve a mover la fuente.
URL_69B = os.getenv(
    "SAT_URL_69B",
    "https://wu1agsprosta001.blob.core.windows.net/agsc-publicaciones"
    "/Datos_abiertos/Documents_AGAFF/Listado_completo_69-B.csv",
)
HEADER_ROW_69B = 3

# 69-B Bis (transmisión indebida de pérdidas fiscales): un archivo, 12 columnas,
# 2 líneas de nota antes del encabezado (header_row = 3, igual que 69-B). Lista
# chica (decenas). El SAT la sirve como CSV Latin-1 aunque el archivo se llame
# .xls. La URL de descarga NO es un path abierto obvio, así que va por env; si
# SAT_URL_69B_BIS está vacío, la ingesta del 69-B Bis se omite (feature-flag).
URL_69B_BIS = os.getenv("SAT_URL_69B_BIS", "")
HEADER_ROW_69B_BIS = 3

# 69 (situación fiscal firme): varios archivos, 6 columnas, encabezado en línea 1.
# Estos siguen en omawww (no tienen equivalente en el blob de Azure); overrideable
# por env por consistencia con URL_69B.
BASE_69 = os.getenv("SAT_BASE_69", "http://omawww.sat.gob.mx/cifras_sat/Documents/")
FILES_69 = (
    "Firmes.csv",
    "Cancelados.csv",
    "NoLocalizados.csv",
    "Exigibles.csv",
    "Sentencias.csv",
    "Condonados.csv",
)

# Los CSV del SAT vienen en Latin-1 (ISO-8859-1).
SOURCE_ENCODING = "ISO-8859-1"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _build_fetch_proxy() -> str:
    """URL del proxy para la descarga de los CSV.

    Prioridad: FETCH_PROXY (URL completa) → credenciales DATAIMPULSE_*.
    Vacío = descarga directa. En pruebas el SAT NO bloqueó IPs de datacenter,
    así que el proxy es opcional (a diferencia de la SSC). Se deja por paridad
    y resiliencia si el SAT llegara a geo/rate-limitar.
    """
    explicit = os.getenv("FETCH_PROXY", "").strip()
    if explicit:
        return explicit
    user = os.getenv("DATAIMPULSE_USER", "").strip()
    pwd = os.getenv("DATAIMPULSE_PASS", "").strip()
    if not (user and pwd):
        return ""
    host = os.getenv("DATAIMPULSE_HOST", "gw.dataimpulse.com").strip()
    port = os.getenv("DATAIMPULSE_PORT", "823").strip()
    country = os.getenv("DATAIMPULSE_COUNTRY", "").strip()
    label = f"{user}__cr.{country}" if country else user
    return f"http://{label}:{pwd}@{host}:{port}"


@dataclass(frozen=True)
class Settings:
    """Snapshot inmutable de la configuración derivada del entorno."""

    # --- Fuentes ---
    url_69b: str = URL_69B
    header_row_69b: int = HEADER_ROW_69B
    url_69b_bis: str = URL_69B_BIS
    header_row_69b_bis: int = HEADER_ROW_69B_BIS
    base_69: str = BASE_69
    files_69: tuple[str, ...] = FILES_69
    source_encoding: str = SOURCE_ENCODING
    download_timeout: int = _env_int("DOWNLOAD_TIMEOUT", 180)
    chunk_size: int = _env_int("CHUNK_SIZE", 1000)

    # Proxy SOLO para descargar del SAT (opcional). OCR no aplica; Turso no usa proxy.
    fetch_proxy: str = _build_fetch_proxy()

    # --- Base de datos (SQLite local; efímero /tmp en Render) ---
    db_path: Path = Path(
        os.getenv("DB_PATH", str(_REPO_ROOT / "data" / "sat69.db"))
    )

    # --- HTTP / despliegue ---
    # Bearer estático: protege /refresh y /reload siempre, y /mcp si OAuth off.
    mcp_api_key: str = os.getenv("MCP_API_KEY", "").strip()
    fetch_on_startup: bool = _env_bool("FETCH_ON_STARTUP", True)
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = _env_int("PORT", 8080)

    # --- OAuth (WorkOS AuthKit) ---
    authkit_domain: str = os.getenv("AUTHKIT_DOMAIN", "").strip()
    base_url: str = (os.getenv("BASE_URL") or os.getenv("MCP_BASE_URL") or "").strip()

    # --- Turso (persistencia durable) ---
    turso_url: str = os.getenv("TURSO_DATABASE_URL", "").strip()
    turso_token: str = os.getenv("TURSO_AUTH_TOKEN", "").strip()

    # --- Freemium / quota (límite diario por usuario; el contador vive en Turso) ---
    free_daily_limit: int = _env_int("SAT69_FREE_DAILY_LIMIT", 1)
    paid_user_ids: frozenset[str] = frozenset(
        x.strip() for x in os.getenv("SAT69_PAID_USER_IDS", "").split(",") if x.strip()
    )

    # --- Capa IA (SOLO para resumen_cartera; el veredicto es determinista) ---
    # Proveedor por env var; default Qwen (DashScope OpenAI-compatible).
    llm_provider: str = os.getenv("LLM_PROVIDER", "qwen").strip().lower()
    llm_model: str = os.getenv("LLM_MODEL", "").strip()
    dashscope_key: str = (os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY") or "").strip()
    dashscope_base_url: str = os.getenv(
        "DASHSCOPE_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    ).strip()
    anthropic_key: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
    gemini_key: str = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()

    # --- Logging ---
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

    @property
    def turso_enabled(self) -> bool:
        return bool(self.turso_url)

    @property
    def oauth_enabled(self) -> bool:
        """OAuth (AuthKit) activo sólo si hay dominio AuthKit y base_url."""
        return bool(self.authkit_domain and self.base_url)


settings = Settings()
