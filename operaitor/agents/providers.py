"""LLM providers a session can connect to. All of them speak the OpenAI-compatible chat API, so the
agents use one client (langchain-openai) for every provider, including Ollama.

Free options first: Groq, Google Gemini and OpenRouter have free tiers (the visitor creates a key
on their site), and Ollama is free and local. OpenAI is paid by whoever owns the key. Model lists
are suggestions (providers rename models over time); any model name the provider accepts works.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import os
import socket
from typing import Any
from urllib.parse import urlparse

PROVIDERS: dict[str, dict] = {
    "groq": {"label": "Groq (free tier)", "base_url": "https://api.groq.com/openai/v1", "free": True,
             "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen/qwen3-32b"],
             "json_mode": True, "key": True, "check": "/models", "signup": "https://console.groq.com/keys"},
    "gemini": {"label": "Google Gemini (free tier)", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "free": True, "models": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"],
               "json_mode": False, "key": True, "check": "/models", "signup": "https://aistudio.google.com/apikey"},
    "openrouter": {"label": "OpenRouter (free models)", "base_url": "https://openrouter.ai/api/v1", "free": True,
                   "models": ["meta-llama/llama-3.3-70b-instruct:free", "qwen/qwen3-235b-a22b:free",
                              "google/gemma-3-27b-it:free"],
                   "json_mode": False, "key": True, "check": "/key", "signup": "https://openrouter.ai/keys"},
    "ollama": {"label": "Ollama (your own server)", "base_url": None, "free": True,
               "models": ["qwen3:8b", "llama3.2", "mistral", "llama3.1:8b"],
               "json_mode": True, "key": False, "check": "/api/tags", "signup": "https://ollama.com/download"},
    "openai": {"label": "OpenAI (paid by you)", "base_url": "https://api.openai.com/v1", "free": False,
               "models": ["gpt-5-nano", "gpt-5-mini"], "json_mode": True, "key": True, "check": "/models",
               "signup": "https://platform.openai.com/api-keys"},
}

OLLAMA_LOCAL = os.environ.get("PLANT_OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("PLANT_OLLAMA_MODEL", "qwen3:8b")


@dataclass(frozen=True)
class Endpoint:
    """Where a session's LLM calls go. ``api_key`` is never logged or sent back to a browser.

    With ``relay`` set, the visitor's browser makes the calls with a key only it holds
    (``browser_llm.py``); ``api_key`` is then empty."""
    provider: str
    base_url: str
    api_key: str
    model: str
    json_mode: bool
    relay: Any = field(default=None, compare=False, repr=False)

    @property
    def in_browser(self) -> bool:
        return self.relay is not None

    def public(self) -> dict:
        p = PROVIDERS.get(self.provider, {})
        return {"provider": self.provider, "label": p.get("label", self.provider), "model": self.model,
                "free": bool(p.get("free")), "url": self.base_url if self.provider == "ollama" else None,
                "in_browser": self.in_browser}


def _ollama_root(url: str) -> str:
    u = url.strip().rstrip("/")
    return u[:-3] if u.endswith("/v1") else u


def check_public_url(url: str) -> str | None:
    """Reasons a visitor-supplied URL may not be used from a public server (SSRF guard), or None."""
    u = urlparse(url)
    if u.scheme != "https":
        return "use an https:// address (e.g. an Ollama exposed through a tunnel)"
    if not u.hostname:
        return "not a valid URL"
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(u.hostname, u.port or 443)}
    except OSError:
        return f"cannot resolve {u.hostname}"
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return "that address is on a private network; the public server cannot reach it"
    return None


def endpoint_for(provider: str, model: str, api_key: str = "", url: str = "") -> Endpoint:
    p = PROVIDERS[provider]
    base = p["base_url"] if provider != "ollama" else _ollama_root(url or OLLAMA_LOCAL) + "/v1"
    return Endpoint(provider, base, api_key or ("ollama" if provider == "ollama" else ""), model, p["json_mode"])


async def check_endpoint(ep: Endpoint, public: bool) -> tuple[bool, str]:
    """Cheap reachability / key check (no tokens spent)."""
    import httpx
    p = PROVIDERS[ep.provider]
    if public and ep.provider == "ollama":
        why = check_public_url(ep.base_url)
        if why:
            return False, why
    if p["key"] and not ep.api_key:
        return False, f"{p['label']} needs an API key ({p['signup']})"
    url = (_ollama_root(ep.base_url) if ep.provider == "ollama" else ep.base_url) + p["check"]
    headers = {"Authorization": f"Bearer {ep.api_key}"} if p["key"] else {}
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            r = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return False, f"could not reach {p['label']} ({type(exc).__name__})"
    if r.status_code == 200:
        if ep.provider == "ollama":
            names = {m.get("name", "") for m in r.json().get("models", [])}
            if names and ep.model not in names and f"{ep.model}:latest" not in names:
                return False, f"this Ollama has no model {ep.model!r}; available: {', '.join(sorted(names)[:8])}"
        return True, "connected"
    if r.status_code in (401, 403):
        return False, f"{p['label']} rejected this key ({r.status_code})"
    return False, f"{p['label']} answered {r.status_code}"


def local_ollama_models() -> list[str]:
    """Models installed in the Ollama server on this machine ([] if it is not running)."""
    import httpx
    try:
        r = httpx.get(_ollama_root(OLLAMA_LOCAL) + "/api/tags", timeout=1.5)
        return sorted(m.get("name", "") for m in r.json().get("models", []) if m.get("name"))
    except Exception:  # noqa: BLE001 - not running / not installed
        return []


def local_ollama_model() -> str | None:
    """The default local Ollama model if an Ollama server is running here (free fallback), else None."""
    names = local_ollama_models()
    if not names:
        return None
    for want in (DEFAULT_OLLAMA_MODEL, f"{DEFAULT_OLLAMA_MODEL}:latest"):
        if want in names:
            return DEFAULT_OLLAMA_MODEL
    return names[0]
