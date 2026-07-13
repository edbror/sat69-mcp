"""Capa de proveedor LLM (resumen_cartera). El veredicto es determinista;
aquí sólo probamos que la redacción por IA sea conmutable y que degrade a None
sin romper cuando no hay proveedor o falla."""
from types import SimpleNamespace

import httpx

from sat69 import llm


def _settings(**over):
    base = dict(
        llm_provider="qwen", llm_model="", dashscope_key="",
        dashscope_base_url="https://dashscope/v1", anthropic_key="", gemini_key="",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_resumir_none_sin_key(monkeypatch):
    monkeypatch.setattr(llm, "settings", _settings(dashscope_key=""))
    assert llm.resumir("sys", "usr") is None


def test_resumir_qwen_parsea_y_manda_bien(monkeypatch):
    monkeypatch.setattr(llm, "settings", _settings(dashscope_key="k"))
    cap = {}

    def fake_post(url, **kw):
        cap["url"] = url
        cap["json"] = kw.get("json")
        return _Resp({"choices": [{"message": {"content": "Brief: 3 CRÍTICO. Escalar."}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = llm.resumir("sys", "usr", max_tokens=100)
    assert out == "Brief: 3 CRÍTICO. Escalar."
    assert "chat/completions" in cap["url"]
    assert cap["json"]["messages"][0]["role"] == "system"
    assert cap["json"]["messages"][1]["content"] == "usr"


def test_resumir_falla_devuelve_none(monkeypatch):
    monkeypatch.setattr(llm, "settings", _settings(dashscope_key="k"))

    def boom(*a, **k):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr(httpx, "post", boom)
    assert llm.resumir("sys", "usr") is None
