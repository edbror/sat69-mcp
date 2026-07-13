"""
Capa de proveedor LLM — SÓLO para el resumen ejecutivo (`resumen_cartera`).

La IA nunca decide riesgo: el veredicto de cada RFC lo calcula el motor de
reglas (determinista, auditable). Aquí el modelo únicamente *redacta* un brief
a partir de esos veredictos ya calculados.

Proveedor conmutable por env var (`LLM_PROVIDER`): `qwen` (default, DashScope
OpenAI-compatible), `anthropic` (Claude) o `gemini`. Vía httpx — sin dependencia
nueva. Si el proveedor no está configurado o falla, `resumir` devuelve None y el
tool cae de vuelta a los datos deterministas.
"""
from __future__ import annotations

import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def resumir(system: str, user: str, max_tokens: int = 700) -> str | None:
    """Texto redactado por el modelo, o None si no hay proveedor / falla."""
    try:
        if settings.llm_provider == "anthropic":
            return _anthropic(system, user, max_tokens)
        if settings.llm_provider == "gemini":
            return _gemini(system, user, max_tokens)
        return _qwen(system, user, max_tokens)  # default
    except Exception as exc:  # noqa: BLE001
        logger.warning("resumen IA falló (%s): %s", settings.llm_provider, exc)
        return None


def _qwen(system: str, user: str, max_tokens: int) -> str | None:
    if not settings.dashscope_key:
        return None
    r = httpx.post(
        f"{settings.dashscope_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.dashscope_key}"},
        json={
            "model": settings.llm_model or "qwen3.7-max",
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=60,
    )
    r.raise_for_status()
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


def _anthropic(system: str, user: str, max_tokens: int) -> str | None:
    if not settings.anthropic_key:
        return None
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": settings.anthropic_key, "anthropic-version": "2023-06-01"},
        json={
            "model": settings.llm_model or "claude-haiku-4-5-20251001",
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=60,
    )
    r.raise_for_status()
    blocks = r.json().get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


def _gemini(system: str, user: str, max_tokens: int) -> str | None:
    if not settings.gemini_key:
        return None
    model = settings.llm_model or "gemini-2.5-flash"
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": settings.gemini_key},
        json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        },
        timeout=60,
    )
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()
