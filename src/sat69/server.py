"""
Servidor FastMCP — Listas del SAT (Art. 69 y 69-B del CFF).

Tools expuestas:
  • verificar_rfc        — verifica un RFC y devuelve veredicto de riesgo
  • verificar_lote       — verifica una lista de RFCs (cartera de proveedores)
  • buscar_nombre        — búsqueda por nombre / razón social (FTS5, sin acentos)
  • estado_datos         — vigencia, conteos y salud del dataset
  • actualizar_datos     — descarga + sincroniza los listados del SAT
"""
from __future__ import annotations

import logging

from fastmcp import FastMCP

from . import database as db
from . import llm
from .config import settings
from .pipeline import process_import

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# Inicializa la base de datos al importar (también la usa web.py).
db.init_db(settings.db_path)


def _build_auth():
    """Provider OAuth (WorkOS AuthKit) si está configurado, o None.

    Con None, /mcp queda protegido por el Bearer estático (ver web.py).
    """
    if not settings.oauth_enabled:
        return None
    import secrets as _secrets

    from fastmcp.server.auth import AccessToken
    from fastmcp.server.auth.providers.workos import (
        AuthKitProvider,
        WorkOSTokenVerifier,
    )

    logger.info("OAuth habilitado vía AuthKit (%s)", settings.authkit_domain)

    class DualVerifier(WorkOSTokenVerifier):
        """Acepta el MCP_API_KEY estático (servidor-a-servidor) O un token OAuth.

        Con OAuth encendido, FastMCP es el único guardián del endpoint MCP y el
        verifier por defecto rechazaba la key estática con 401 invalid_token —
        las apps máquina-a-máquina quedaban fuera (lo detectó el cruce de
        repse-mcp, WE-529; mismo fix que repse PR #4).
        """

        async def verify_token(self, token: str) -> AccessToken | None:
            if settings.mcp_api_key and _secrets.compare_digest(token, settings.mcp_api_key):
                return AccessToken(token=token, client_id="watr-static-key", scopes=[])
            return await super().verify_token(token)

    # Los access tokens de AuthKit (DCR) traen aud = client_id, NO la resource
    # URL del MCP; el JWTVerifier por defecto los rechaza por audience mismatch.
    # WorkOSTokenVerifier valida vía el userinfo de AuthKit (sin chequeo de aud),
    # igual que el MCP de SSC — así no hace falta registrar resource indicators
    # en WorkOS (que rompen otros MCP del mismo entorno).
    return AuthKitProvider(
        authkit_domain=settings.authkit_domain,
        base_url=settings.base_url,
        token_verifier=DualVerifier(authkit_domain=settings.authkit_domain),
    )


mcp = FastMCP(
    name="sat-69-69b",
    auth=_build_auth(),
    instructions="""\
Servidor MCP para consultar las listas del SAT (México):

• Artículo 69-B del CFF (EFOS): contribuyentes con operaciones simuladas.
  Situaciones: Presunto, Desvirtuado, Definitivo, Sentencia Favorable.
  Un "Definitivo" implica que sus CFDI NO producen efectos fiscales.
• Artículo 69 del CFF: contribuyentes con situación fiscal firme
  (firmes, exigibles, no localizados, cancelados, condonados).

Tools:
1. `verificar_rfc` — un RFC → veredicto de riesgo (CRITICO…LIMPIO).
2. `verificar_lote` — cartera de RFCs → sólo los hallazgos, por severidad.
3. `buscar_nombre` — por nombre/razón social cuando no se tiene el RFC.
4. `estado_datos` — vigencia y conteos.
5. `actualizar_datos` — refresca desde los archivos públicos del SAT.

Los resultados reflejan la última importación. No constituyen asesoría fiscal.
""",
)


@mcp.tool
def verificar_rfc(rfc: str) -> dict:
    """Verifica un RFC contra las listas del SAT (Art. 69 y 69-B).

    Args:
        rfc: RFC a consultar (física o moral). Se normaliza automáticamente.

    Returns:
        Veredicto con riesgo (CRITICO|ALTO|MEDIO|BAJO|INFORMATIVO|LIMPIO),
        explicación y los registros encontrados en cada lista.
    """
    if not (rfc or "").strip():
        return {"error": "Debes proporcionar un RFC."}
    return db.verificar_rfc(rfc)


@mcp.tool
def verificar_lote(rfcs: list[str]) -> dict:
    """Verifica una lista de RFCs (p. ej. proveedores). Devuelve sólo hallazgos.

    Args:
        rfcs: Lista de RFCs (máximo 500).

    Returns:
        total_consultados, con_hallazgos, conteo_por_riesgo y coincidencias
        ordenadas por severidad.
    """
    if not rfcs:
        return {"error": 'Debes proporcionar una lista "rfcs" con al menos un RFC.'}
    if len(rfcs) > 500:
        return {"error": "Máximo 500 RFCs por lote."}
    return _lote(rfcs)


def _lote(rfcs: list[str]) -> dict:
    """Verificación determinista de un lote (motor de reglas). Reusada por el resumen."""
    conteo = {k: 0 for k in ("CRITICO", "ALTO", "MEDIO", "BAJO", "INFORMATIVO", "LIMPIO")}
    coincidencias: list[dict] = []
    for rfc in rfcs:
        r = db.verificar_rfc(str(rfc))
        conteo[r["riesgo"]] = conteo.get(r["riesgo"], 0) + 1
        if r["riesgo"] != "LIMPIO":
            coincidencias.append({
                "rfc": r["rfc"], "riesgo": r["riesgo"], "veredicto": r["veredicto"],
                "en_69b": r["en_69b"], "en_69": r["en_69"],
            })

    orden = {k: i for i, k in enumerate(["CRITICO", "ALTO", "MEDIO", "INFORMATIVO", "BAJO"])}
    coincidencias.sort(key=lambda c: orden.get(c["riesgo"], 9))
    return {
        "total_consultados": len(rfcs),
        "con_hallazgos": len(coincidencias),
        "conteo_por_riesgo": conteo,
        "coincidencias": coincidencias,
    }


@mcp.tool
def resumen_cartera(rfcs: list[str]) -> dict:
    """Resumen ejecutivo en lenguaje natural de una cartera de RFCs.

    El riesgo de cada RFC lo calcula el motor de reglas (DETERMINISTA); la IA
    sólo redacta el brief a partir de esos veredictos ya calculados — nunca
    decide el riesgo. Proveedor de IA conmutable por env var (LLM_PROVIDER).

    Args:
        rfcs: Lista de RFCs (máximo 500).

    Returns:
        Los datos deterministas (conteo_por_riesgo, coincidencias) + `resumen`
        (texto de IA) y una `nota` de trazabilidad. Si la IA no está configurada,
        `resumen` es None y devuelve igual los datos.
    """
    if not rfcs:
        return {"error": 'Debes proporcionar una lista "rfcs" con al menos un RFC.'}
    if len(rfcs) > 500:
        return {"error": "Máximo 500 RFCs por lote."}

    import json

    data = _lote(rfcs)  # veredictos deterministas

    system = (
        "Eres analista de cumplimiento fiscal en México. Redacta un brief "
        "ejecutivo, breve y accionable, sobre una cartera de proveedores ya "
        "clasificada por un motor de reglas del SAT (Art. 69 y 69-B del CFF). "
        "Usa SÓLO los datos dados; no inventes cifras ni cambies los veredictos. "
        "Prioriza CRITICO (EFOS definitivo: los CFDI no son deducibles) y ALTO. "
        "Español, directo. No es asesoría fiscal."
    )
    user = (
        f"Total consultados: {data['total_consultados']}. "
        f"Conteo por riesgo: {json.dumps(data['conteo_por_riesgo'], ensure_ascii=False)}.\n"
        f"Hallazgos (hasta 40, por severidad):\n"
        f"{json.dumps(data['coincidencias'][:40], ensure_ascii=False)}"
    )
    resumen = llm.resumir(system, user)

    nota = ("Veredictos deterministas (motor de reglas); el resumen es redacción de "
            "IA y no altera los veredictos.")
    if resumen is None:
        nota += " IA no configurada o no disponible: usa los datos deterministas."
    return {**data, "resumen": resumen, "nota": nota}


@mcp.tool
def buscar_nombre(texto: str, dataset: str = "ambos", limite: int = 25) -> dict:
    """Busca contribuyentes por nombre / razón social parcial (FTS5, sin acentos).

    Args:
        texto: Fragmento del nombre (mínimo 3 caracteres).
        dataset: "69", "69b" o "ambos" (default).
        limite: Máximo de resultados por lista (1–100, default 25).

    Returns:
        Coincidencias por lista con RFC, nombre y situación/supuesto.
    """
    if len((texto or "").strip()) < 3:
        return {"error": "El texto de búsqueda debe tener al menos 3 caracteres."}
    if dataset not in ("69", "69b", "ambos"):
        dataset = "ambos"
    try:
        resultados = db.buscar_nombre(texto, dataset, limite)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Error en la búsqueda: {exc}"}
    total = sum(len(v) for v in resultados.values())
    return {"resultados": resultados, "count": total, "texto": texto}


@mcp.tool
def estado_datos() -> dict:
    """Estado del dataset: vigencia declarada por el SAT, conteos y última importación."""
    return db.estado_datos()


@mcp.tool
def actualizar_datos(dataset: str = "all", force_refresh: bool = False) -> dict:
    """Descarga y sincroniza los listados del SAT (idempotente por hash).

    Args:
        dataset: "all" (default), "69" o "69b".
        force_refresh: Reprocesa aunque el archivo no haya cambiado.

    Returns:
        Resumen con filas importadas por archivo y errores.
    """
    return process_import(dataset=dataset, force_refresh=force_refresh)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
