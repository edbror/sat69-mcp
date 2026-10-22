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
from dataclasses import dataclass, fields
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
# chica (decenas). Vive en el mismo blob de Azure que el 69-B pero bajo
# Documents_AGGC (no AGAFF). Overrideable por env; `SAT_URL_69B_BIS=""` (vacío
# explícito) desactiva la ingesta del 69-B Bis (feature-flag).
URL_69B_BIS = os.getenv(
    "SAT_URL_69B_BIS",
    "https://wu1agsprosta001.blob.core.windows.net/agsc-publicaciones"
    "/Datos_abiertos/Documents_AGGC/Listado_69_B_Bis_Completo.csv",
)
HEADER_ROW_69B_BIS = 3

# CSD sin efectos (certificados de sello digital cancelados, Art. 17-H CFF): un
# archivo, 6 columnas, encabezado en línea 1 (header_row = 1, como el 69). Lista
# grande (~60k). Vive en el blob de Azure bajo Documents_AGR, que es la
# administración que cancela (coincide con la columna de la lista).
# Overrideable por env por consistencia; NO lleva feature-flag ni secret en el
# cron (es pública y horneada) — así evita el trap del 69-B Bis (secret vacío que
# apagaba la ingesta).
#
# 2026-10: estaba apuntando a omawww, que quedó CONGELADO en el corte del
# 3-dic-2025 y seguía dando 200. Es el MISMO trap que el 69-B en julio, y aquí
# costó 8,814 RFC con el CSD cancelado que respondíamos como limpios. La fuente
# viva es la que linkea la propia página de datos abiertos del SAT.
URL_CSD = os.getenv(
    "SAT_URL_CSD",
    "https://wu1agsprosta001.blob.core.windows.net/agsc-publicaciones"
    "/Datos_abiertos/Documents_AGR/CSDsinefectos.csv",
)
HEADER_ROW_CSD = 1

# 69 (situación fiscal firme): varios archivos, 6 columnas, encabezado en línea 1.
# Mismo blob que el CSD y por la misma razón: en omawww estos cinco quedaron en el
# corte del 17-ene-2026 y `NoLocalizados.csv` en el del 7-ene-2025.
#
# Dos diferencias de la fuente viva que hay que respetar:
#   1. `No_localizados.csv` se llama así (guion bajo, ele minúscula). El nombre
#      viejo da 404 en Azure.
#   2. `Condonados.csv` NO existe: el SAT lo partió en cinco archivos (reducción
#      art. 74, condonados 146-B, recargos art. 21, por decreto, y el histórico
#      2007-2015). Se deja fuera hasta decidir cuáles entran, en vez de seguir
#      importando un archivo que el SAT ya no publica. Ver ESTADO.md.
BASE_69 = os.getenv(
    "SAT_BASE_69",
    "https://wu1agsprosta001.blob.core.windows.net/agsc-publicaciones"
    "/Datos_abiertos/Documents_AGR/",
)
FILES_69 = (
    "Firmes.csv",
    "Cancelados.csv",
    "No_localizados.csv",
    "Exigibles.csv",
    "Sentencias.csv",
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


# Campos que NUNCA se imprimen. `fetch_proxy` entra porque es una URL con la
# contraseña embebida (`http://user:pwd@host:port`).
_SECRETOS = frozenset({
    "mcp_api_key",
    "turso_token",
    "dashscope_key",
    "anthropic_key",
    "gemini_key",
    "fetch_proxy",
})


@dataclass(frozen=True)
class Settings:
    """Snapshot inmutable de la configuración derivada del entorno."""

    # --- Fuentes ---
    url_69b: str = URL_69B
    header_row_69b: int = HEADER_ROW_69B
    url_69b_bis: str = URL_69B_BIS
    header_row_69b_bis: int = HEADER_ROW_69B_BIS
    url_csd: str = URL_CSD
    header_row_csd: int = HEADER_ROW_CSD
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

    # Escotilla de un solo uso para migrar de fuente: acepta una merma grande de
    # filas en vez de conservar lo anterior. Fuera de una migración hay que
    # apagarla, porque es justo el guard que atrapa una descarga truncada.
    permitir_merma: bool = _env_bool("SAT_PERMITIR_MERMA", False)

    # --- Logging ---
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

    def __repr__(self) -> str:
        """Igual que el de dataclass pero con los secretos tapados.

        El repr de un dataclass imprime TODOS los campos, así que cualquier
        `assert settings.x == y` que falle, cualquier log de la config y cualquier
        excepción que la arrastre publicaba las llaves en claro. Ya pasó: una
        aserción de pytest escupió `gemini_key` completa, y los logs de CI son
        públicos para quien tenga el repo.
        """
        partes = []
        for campo in fields(self):
            valor = getattr(self, campo.name)
            if campo.name in _SECRETOS and valor:
                valor = f"<{campo.name} presente, {len(str(valor))} chars>"
            partes.append(f"{campo.name}={valor!r}")
        return f"{type(self).__name__}({', '.join(partes)})"

    @property
    def turso_enabled(self) -> bool:
        return bool(self.turso_url)

    @property
    def oauth_enabled(self) -> bool:
        """OAuth (AuthKit) activo sólo si hay dominio AuthKit y base_url."""
        return bool(self.authkit_domain and self.base_url)


settings = Settings()
