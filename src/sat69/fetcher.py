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

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_FETCH_RETRIES = 4


def fetch_csv(url: str, timeout: int | None = None) -> bytes:
    """Descarga un CSV y devuelve los bytes crudos (con reintentos + backoff)."""
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
                return r.content
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt == _FETCH_RETRIES:
                    break
                wait = min(2 ** attempt, 15)
                logger.warning("Descarga falló (%s) — reintento en %ds", exc, wait)
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
