"""
Punto de entrada HTTP para el despliegue en Render.

Envuelve el servidor FastMCP con:
  • Transporte Streamable HTTP (MCP-over-HTTP).
  • Middleware de auth Bearer (MCP_API_KEY) selectiva por ruta.
  • Arranque: pull de Turso → SQLite local, e ingesta en segundo plano si vacío.
  • /health (sin auth) para los health checks de Render.
  • /verificar (bearer siempre) — el mismo veredicto que la tool, en REST plano,
    para backends que no hablan MCP.
  • /verificar-lote (bearer siempre) — hasta 500 RFC por llamada.

Arranque:
    python -m sat69.web   |   sat69-web
"""
from __future__ import annotations

import logging
import threading

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from .config import settings

logger = logging.getLogger(__name__)

# Ruta del endpoint MCP. "/connect", igual que el MCP de SSC.
MCP_PATH = "/connect"


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Bearer estático (MCP_API_KEY) selectivo por ruta.

    - /health          → siempre abierto.
    - /refresh,/reload,/verificar,/verificar-lote → siempre Bearer (máquina-a-máquina).
    - /mcp y metadata OAuth:
        · OAuth ON  → pasa (FastMCP/AuthKit maneja la auth de /mcp).
        · OAuth OFF → exige el Bearer estático.
    No-op si MCP_API_KEY no está definido (desarrollo local).
    """

    def _needs_static_bearer(self, path: str) -> bool:
        if path == "/health":
            return False
        if path in ("/refresh", "/reload", "/verificar", "/verificar-lote"):
            return True
        return not settings.oauth_enabled

    async def dispatch(self, request: Request, call_next):
        if not settings.mcp_api_key or not self._needs_static_bearer(request.url.path):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "Falta el header Authorization"},
                status_code=401, headers={"WWW-Authenticate": "Bearer"},
            )
        if auth[len("Bearer "):] != settings.mcp_api_key:
            return JSONResponse(
                {"error": "Token inválido"},
                status_code=401, headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------

def _run_startup() -> None:
    logger.info("=== SAT 69/69-B MCP arrancando ===")
    from . import database as db
    from .turso import pull_from_turso

    try:
        pulled = pull_from_turso()
        logger.info("Pull de Turso: %d fila(s) importadas", pulled)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pull de Turso falló (no fatal): %s", exc)

    if settings.fetch_on_startup:
        status = db.estado_datos()
        if status.get("status") == "no_data":
            logger.info("DB vacía — lanzando ingesta en segundo plano…")
            threading.Thread(target=_import_in_background, name="import", daemon=True).start()
        else:
            logger.info("DB con datos — sin auto-import")


def _import_in_background() -> None:
    try:
        from .pipeline import process_import
        result = process_import()
        logger.info("Ingesta en segundo plano terminó: %s", result.get("message"))
    except Exception as exc:  # noqa: BLE001
        logger.error("Ingesta en segundo plano falló: %s", exc)


# ---------------------------------------------------------------------------
# Endpoints de servicio
# ---------------------------------------------------------------------------

async def _health(request: Request) -> PlainTextResponse:
    from . import database as db
    s = db.estado_datos()
    body = "\n".join([
        "ok",
        f"status: {s.get('status', 'unknown')}",
        f"69:     {s.get('art_69', {}).get('total_registros', 0)}",
        f"69b:    {s.get('art_69b', {}).get('total_registros', 0)}",
        f"import: {s.get('ultima_importacion') or 'never'}",
    ])
    return PlainTextResponse(body)


async def _refresh(request: Request) -> JSONResponse:
    from starlette.concurrency import run_in_threadpool

    from .pipeline import process_import
    force = request.query_params.get("force", "").lower() in {"1", "true", "yes"}
    dataset = request.query_params.get("dataset", "all")
    logger.info("Refresh solicitado (dataset=%s, force=%s)", dataset, force)
    result = await run_in_threadpool(process_import, dataset, force)
    return JSONResponse(result, status_code=200 if result.get("success") else 502)


MAX_LOTE = 500


async def _indice_listo():
    """Devuelve (estado, None) si hay datos del 69-B, o (None, respuesta 503).

    Si la tabla del 69-B está vacía —arranque en frío, pull de Turso a medias,
    ingesta fallida— `verificar_rfc` devolvería LIMPIO para todo RFC del mundo, y
    quien llame lo guardaría como "verificado, sin hallazgos". Eso certifica como
    limpio a un EFOS definitivo.

    Ojo con el matiz: `estado_datos()` sólo dice `no_data` cuando las TRES tablas
    están vacías. Aquí se checa el 69-B por separado, porque es la lista que decide.
    """
    from starlette.concurrency import run_in_threadpool

    from . import database as db

    estado = await run_in_threadpool(db.estado_datos)
    if estado.get("status") != "ok" or not (estado.get("art_69b") or {}).get("total_registros"):
        return None, JSONResponse(
            {
                "error": "Índice sin datos del 69-B; no se puede emitir veredicto.",
                "status": estado.get("status"),
            },
            status_code=503,
        )
    return estado, None


async def _verificar_lote(request: Request) -> JSONResponse:
    """Veredicto de hasta 500 RFC en una llamada.

    Nace de MATERIA (WE-514): monitorear 1,641 proveedores de uno en uno son 1,641
    peticiones HTTP, medidas en ~2.5 s cada una — 68 minutos por barrido, y el cron
    se corta a los 5. Por lote son 4 llamadas.

    **Devuelve TODOS los RFC, no sólo los hallazgos**, a diferencia de la tool
    `verificar_lote` del MCP. Es deliberado: la tool le habla a un modelo, que quiere
    la señal; esto le habla a un expediente de defensa, donde "lo verifiqué y no
    apareció" es evidencia de diligencia y tiene que quedar registrado con su fecha.
    Omitir los limpios sería omitir la mitad de la prueba.
    """
    from starlette.concurrency import run_in_threadpool

    from . import database as db

    try:
        cuerpo = await request.json()
    except Exception:
        return JSONResponse({"error": "Se espera un JSON con la clave `rfcs`."}, status_code=400)

    rfcs = cuerpo.get("rfcs") if isinstance(cuerpo, dict) else None
    if not isinstance(rfcs, list) or not rfcs:
        return JSONResponse({"error": "Falta la lista `rfcs`."}, status_code=400)
    if len(rfcs) > MAX_LOTE:
        return JSONResponse(
            {"error": f"Máximo {MAX_LOTE} RFC por lote; llegaron {len(rfcs)}."},
            status_code=400,
        )

    estado, error = await _indice_listo()
    if error is not None:
        return error

    vigencia = (estado.get("art_69b") or {}).get("sat_actualizado_al")

    def _todos() -> dict:
        salida = {}
        for rfc in rfcs:
            r = db.verificar_rfc(str(rfc))
            r["sat_actualizado_al"] = vigencia
            salida[str(rfc)] = r
        return salida

    return JSONResponse(
        {
            "total": len(rfcs),
            "sat_actualizado_al": vigencia,
            "resultados": await run_in_threadpool(_todos),
        }
    )


async def _verificar(request: Request) -> JSONResponse:
    """Veredicto de un RFC en REST plano, para servidor-a-servidor.

    Existe porque hablar MCP desde un backend es un handshake de sesión completo
    para una pregunta de una línea. Llama a la MISMA `database.verificar_rfc` que
    la tool, sin capa de traducción, para que las dos no se desincronicen.

    **El guardia que importa.** Si la tabla del 69-B está vacía —arranque en frío,
    pull de Turso a medias, ingesta fallida— `verificar_rfc` devolvería LIMPIO para
    todo RFC del mundo. Un consumidor lo guardaría como "proveedor verificado y sin
    hallazgos", que es una constancia falsa. Así que se responde 503: quien llama
    debe registrar "no pude verificar", nunca "está limpio".

    Ojo con el matiz: `estado_datos()` sólo dice `no_data` cuando las TRES tablas
    están vacías. Aquí se checa el 69-B por separado, porque es la lista que decide.
    """
    from starlette.concurrency import run_in_threadpool

    from . import database as db

    rfc = (request.query_params.get("rfc") or "").strip()
    if not rfc:
        return JSONResponse({"error": "Falta el parámetro `rfc`."}, status_code=400)

    estado, error = await _indice_listo()
    if error is not None:
        return error

    resultado = await run_in_threadpool(db.verificar_rfc, rfc)
    # La vigencia del dato es parte de la evidencia: en un expediente de defensa
    # importa contra qué corte del SAT se verificó, no sólo qué día se preguntó.
    resultado["sat_actualizado_al"] = (estado.get("art_69b") or {}).get("sat_actualizado_al")
    return JSONResponse(resultado)


async def _oauth_protected_resource_root(request: Request) -> JSONResponse:
    """Metadata OAuth (RFC 9728) servida en la RAÍZ.

    FastMCP ≥3.4.x sólo la registra con sufijo (/.well-known/oauth-protected-resource/mcp),
    pero algunos clientes (el connector del Claude app) la buscan en la raíz —
    igual que la sirve el MCP de SSC. Sin esto, el discovery OAuth da 404 y el
    connector falla con "authorization failed".
    """
    return JSONResponse({
        "resource": f"{settings.base_url}{MCP_PATH}",
        "authorization_servers": [settings.authkit_domain],
        "scopes_supported": [],
        "bearer_methods_supported": ["header"],
    })


async def _reload(request: Request) -> JSONResponse:
    from starlette.concurrency import run_in_threadpool

    from . import database as db
    from .turso import pull_from_turso
    logger.info("Reload solicitado: pull desde Turso")
    imported = await run_in_threadpool(pull_from_turso)
    return JSONResponse({"success": True, "rows_imported": imported, "status": db.estado_datos()})


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> Starlette:
    from .server import mcp  # importar configura la DB
    mcp_app = mcp.http_app(path=MCP_PATH, transport="http")
    routes = [
        Route("/health", _health, methods=["GET"]),
        Route("/refresh", _refresh, methods=["POST"]),
        Route("/reload", _reload, methods=["POST"]),
        Route("/verificar", _verificar, methods=["GET"]),
        Route("/verificar-lote", _verificar_lote, methods=["POST"]),
    ]
    if settings.oauth_enabled:
        routes.append(Route(
            "/.well-known/oauth-protected-resource",
            _oauth_protected_resource_root, methods=["GET"],
        ))
    routes.append(Mount("/", app=mcp_app))
    return Starlette(
        routes=routes,
        middleware=[Middleware(BearerAuthMiddleware)],
        lifespan=mcp_app.lifespan,
    )


def main() -> None:
    app = create_app()
    # El pull de Turso (½M filas, ~5 min) corre en segundo plano para que uvicorn
    # abra el puerto de inmediato; si no, Render no detecta el puerto y reinicia
    # la instancia en bucle. /health reporta no_data hasta que termina el pull.
    threading.Thread(target=_run_startup, name="startup", daemon=True).start()
    uvicorn.run(
        app, host=settings.host, port=settings.port,
        log_level=settings.log_level.lower(), timeout_graceful_shutdown=10,
    )


if __name__ == "__main__":
    main()
