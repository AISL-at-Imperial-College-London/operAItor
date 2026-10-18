"""LLM providers: endpoints and their public view (no network)."""
from __future__ import annotations

from operaitor.agents import providers as P
from operaitor.agents.providers import Endpoint, endpoint_for


def test_endpoints_and_public_view_hide_the_key():
    ep = endpoint_for("groq", "llama-3.3-70b-versatile", "gsk-secret")
    assert ep.base_url == P.PROVIDERS["groq"]["base_url"] and ep.json_mode is True
    assert "gsk-secret" not in str(ep.public()) and ep.public()["in_browser"] is False
    oll = endpoint_for("ollama", "qwen3:8b", "", "https://my.example.org/v1/")
    assert oll.base_url == "https://my.example.org/v1" and oll.api_key == "ollama"
    assert oll.public()["url"] == "https://my.example.org/v1"
    assert endpoint_for("ollama", "llama3.2").base_url.endswith(":11434/v1")
    assert Endpoint("groq", "b", "", "m", True, relay=object()).in_browser

