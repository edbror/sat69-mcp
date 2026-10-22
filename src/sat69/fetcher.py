"""
Descarga de los CSV de datos abiertos del SAT.

Si settings.fetch_proxy está definido, la descarga sale por ese proxy
(residencial). En pruebas el SAT no bloquea IPs de datacenter, así que el proxy
es opcional; se conserva por paridad con el MCP de movilizaciones.
"""
from __future__ import annotations

import hashlib
import logging
import time
from email.utils import parsedate_to_datetime

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_FETCH_RETRIES = 4


def fetch_csv(url: str, timeout: int | None = None) -> tuple[bytes, str | None]:
    """Descarga un CSV y devuelve (bytes crudos, Last-Modified en ISO o None).

    El `Last-Modified` no es un extra: para el 69 y el CSD es la ÚNICA señal de
    vigencia que existe, porque esos archivos no traen la nota "actualizado al"
    que sí trae el 69-B. Sin leerlo, una fuente congelada que sigue dando 200 se
    ve exactamente igual que una que no ha cambiado, y eso ya pasó dos veces.
    """
    timeout = timeout or settings.download_timeout
    headers = {"User-Agent": "SAT69-MCP/1.0", "Cache-Control": "no-cache"}
    proxy = settings.fetch_proxy or None
    if proxy:
        logger.info("Descarga vía proxy configurado")
    last_exc: Exception | None = None
    with httpx.Client(
        timeout=timeout, follow_redirects=True, headers=headers, proxy=proxy
    ) as client:
        for attempt in range(1, _FETCH_RETRIES + 1):
            try:
                logger.info("Descargando %s (intento %d)", url, attempt)
                r = client.get(url)
                r.raise_for_status()
                logger.info("Descargados %d bytes", len(r.content))
                return r.content, _last_modified_iso(r.headers.get("last-modified"))
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt == _FETCH_RETRIES:
                    break
                wait = min(2 ** attempt, 15)
                logger.warning("Descarga falló (%s) — reintento en %ds", exc, wait)
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def _last_modified_iso(cabecera: str | None) -> str | None:
    """"Wed, 03 Dec 2025 19:59:49 GMT" → "2025-12-03". None si no viene o no parsea."""
    if not cabecera:
        return None
    try:
        return parsedate_to_datetime(cabecera).date().isoformat()
    except (TypeError, ValueError):
        logger.warning("Last-Modified no parseable: %r", cabecera)
        return None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
