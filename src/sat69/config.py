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
from dataclasses import dataclass, field
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_ROOT.parents[1]

# --- Fuentes de datos abiertos del SAT ------------------------------------
# 69-B (EFOS): un archivo, 20 columnas, 2 líneas de nota antes del encabezado
# real (por eso header_row = 3, basado en 1).
URL_69B = "http://omawww.sat.gob.mx/cifras_sat/Documents/Listado_Completo_69-B.csv"
HEADER_ROW_69B = 3

# 69 (situación fiscal firme): varios archivos, 6 columnas, encabezado en línea 1.
BASE_69 = "http://omawww.sat.gob.mx/cifras_sat/Documents/"
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
