"""Chat-model factory shared by the diagnosis, planning, action, judge and chat agents.

Every provider goes through the OpenAI-compatible API (langchain-openai):
  * local Ollama (free): any model name that is not an OpenAI one, e.g. ``qwen3:8b``, ``llama3.2``;
    server at PLANT_OLLAMA_URL (default http://127.0.0.1:11434). Used by default when no
    OpenAI key is set and Ollama is running.
  * OpenAI: ``gpt-*`` / ``o<digit>`` names with OPENAI_API_KEY (environment or ``.env``).
  * any OpenAI-compatible server: PLANT_LLM_BASE_URL (+ PLANT_LLM_API_KEY, PLANT_LLM_JSON_MODE=0
    if the server rejects response_format).
  * per session (web console): the visitor connects Groq / Gemini / OpenRouter (free tiers), their
    own Ollama URL, or OpenAI (``providers.py``). ``use_session_llm(endpoint, usage)`` binds that
    endpoint and a usage meter through context variables, so each visitor's agents use their own
    connection and are metered separately. ``PLANT_HOSTED=1``: only visitor-connected LLMs are used.

PLANT_LLM_MAX_RETRIES sets retries on rate limits / timeouts (default 2).

DEFAULT_MODEL is the cheapest OpenAI text model on the pricing page when this
was written (gpt-5-nano, $0.05 / $0.40 per 1M input / output tokens,
https://developers.openai.com/api/docs/pricing, checked 2026-10-05). GPT-5 /
o-series models are reasoning models: they only accept temperature=1 and take a
``reasoning_effort`` (default "low" here, to keep control-loop latency
down; override with PLANT_REASONING_EFFORT).
"""
from __future__ import annotations

import contextvars
import json
import os
from pathlib import Path
import re
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .providers import Endpoint

DEFAULT_MODEL = "gpt-5-nano"

# USD per 1M tokens (input, output), standard tier, from the pricing page above.
# Update when OpenAI changes prices; unknown models are costed as None.
PRICES_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-6-luna": (0.10, 0.50),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-4.1-mini": (0.40, 1.60),
}

_REPO_ROOT = Path.cwd()  # .env is read from the working directory
_env_loaded = False


def load_env() -> None:
    """Load ``.env`` from the repo root (or $PLANT_DOTENV) once, without overriding real env vars."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    path = Path(os.environ.get("PLANT_DOTENV", _REPO_ROOT / ".env"))
    if path.exists():
        load_dotenv(path, override=False)


_SESSION_ENDPOINT: contextvars.ContextVar["Endpoint | None"] = contextvars.ContextVar("plant_llm_endpoint", default=None)
_SESSION_USAGE: contextvars.ContextVar["UsageMeter | None"] = contextvars.ContextVar("plant_llm_usage", default=None)


def hosted() -> bool:
    """Public deployment: only visitor-connected LLMs are used, never the server's own key or models."""
    return os.environ.get("PLANT_HOSTED", "0") == "1"


def use_session_llm(endpoint: "Endpoint | None", usage: "UsageMeter | None") -> None:
    """Bind an LLM endpoint (provider, URL, key, model) and a usage meter to the current context."""
    _SESSION_ENDPOINT.set(endpoint)
    _SESSION_USAGE.set(usage)


def session_endpoint() -> "Endpoint | None":
    return _SESSION_ENDPOINT.get()


def is_openai_model(model: str) -> bool:
    return model.startswith("gpt") or bool(re.match(r"o\d", model))


def is_reasoning_model(model: str) -> bool:
    return bool(re.match(r"(gpt-5|gpt-6|o\d)", model))


def _needs_langchain_openai() -> str | None:
    try:
        import langchain_openai  # noqa: F401
    except ImportError:
        return 'langchain-openai is not installed (pip install "operaitor[llm]")'
    return None


def llm_status(model: str = DEFAULT_MODEL) -> tuple[bool, str]:
    """Can ``model`` be used right now? Returns (ok, human-readable reason)."""
    load_env()
    if (missing := _needs_langchain_openai()):
        return False, missing
    ep = _SESSION_ENDPOINT.get()
    if ep is not None:
        from .providers import PROVIDERS
        return True, f"{PROVIDERS.get(ep.provider, {}).get('label', ep.provider)} {ep.model}"
    if hosted():
        return False, "no LLM connected for this session (connect one in the Assistant panel)"
    if os.environ.get("PLANT_LLM_BASE_URL"):
        return True, f"{model} at {os.environ['PLANT_LLM_BASE_URL']}"
    if is_openai_model(model):
        if not os.environ.get("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY is not set (put OPENAI_API_KEY=... in .env), or run Ollama for a free local model"
        return True, f"OpenAI {model}"
    try:
        import langchain_ollama  # noqa: F401
    except ImportError:
        return False, 'Ollama models need langchain-ollama (pip install "operaitor[llm]")'
    from .providers import OLLAMA_LOCAL, local_ollama_model
    if local_ollama_model() is None:
        return False, f"Ollama is not running at {OLLAMA_LOCAL} (https://ollama.com/download, then: ollama pull {model})"
    return True, f"Ollama {model} (local, free)"


def make_llm(model: str = DEFAULT_MODEL, timeout: float = 120.0, temperature: float = 0.0,
             json_mode: bool = False, **extra: Any):
    """Build a chat model. Every provider (OpenAI, Groq, Gemini, OpenRouter, Ollama, any
    OpenAI-compatible server) goes through langchain-openai. ``json_mode`` asks for a JSON object."""
    load_env()
    if (missing := _needs_langchain_openai()):
        raise RuntimeError(missing)
    from langchain_openai import ChatOpenAI
    retries = int(os.environ.get("PLANT_LLM_MAX_RETRIES", "2"))
    ep = _SESSION_ENDPOINT.get()
    if (ep is not None and ep.provider == "ollama") or (ep is None and not hosted() and not os.environ.get("PLANT_LLM_BASE_URL")
                                                         and not is_openai_model(model)):
        return _ollama(ep.model if ep else model, (ep.base_url[:-3] if ep else None), timeout, temperature, json_mode, **extra)
    if ep is not None:
        model, base_url, api_key, use_json, provider = ep.model, ep.base_url, ep.api_key, ep.json_mode, ep.provider
    elif hosted():
        raise RuntimeError("no LLM connected for this session")
    elif os.environ.get("PLANT_LLM_BASE_URL"):  # any OpenAI-compatible server (LM Studio, vLLM, llama.cpp, ...)
        base_url, api_key = os.environ["PLANT_LLM_BASE_URL"], os.environ.get("PLANT_LLM_API_KEY", "local")
        use_json, provider = os.environ.get("PLANT_LLM_JSON_MODE", "1") != "0", "custom"
    elif is_openai_model(model):
        base_url, api_key, use_json, provider = None, os.environ.get("OPENAI_API_KEY"), True, "openai"
    kw: dict[str, Any] = dict(model=model, timeout=timeout, max_retries=retries, api_key=api_key, **extra)
    if base_url:
        kw["base_url"] = base_url
    if provider == "openai" and is_reasoning_model(model):
        # GPT-5 / o-series accept only the default temperature (1) and take a reasoning effort.
        kw["temperature"] = 1
        kw["reasoning_effort"] = os.environ.get("PLANT_REASONING_EFFORT", "low")
    else:
        kw["temperature"] = temperature
    llm = ChatOpenAI(**kw)
    return llm.bind(response_format={"type": "json_object"}) if (json_mode and use_json) else llm


def _ollama(model: str, root: str | None, timeout: float, temperature: float, json_mode: bool, **extra: Any):
    """Ollama through its native API: the agents' prompts are ~7-10k tokens, and Ollama's default
    context (and its OpenAI-compatible endpoint) would silently truncate them."""
    try:
        from langchain_ollama import ChatOllama
    except ImportError as exc:
        raise RuntimeError('Ollama models need langchain-ollama (pip install "operaitor[llm]")') from exc
    from .providers import OLLAMA_LOCAL
    return ChatOllama(model=model, base_url=(root or OLLAMA_LOCAL).rstrip("/"), temperature=temperature,
                      num_ctx=int(os.environ.get("PLANT_OLLAMA_NUM_CTX", "16384")),
                      client_kwargs={"timeout": timeout}, **({"format": "json"} if json_mode else {}), **extra)


# ----------------------------------------------------------------------------- usage / cost
class UsageMeter:
    """Thread-safe running totals of LLM calls, tokens and estimated cost per agent."""

    def __init__(self):
        self._lock = threading.Lock()
        self.by_agent: dict[str, dict[str, float]] = {}

    def record(self, agent: str, model: str, msg: Any) -> dict:
        u = getattr(msg, "usage_metadata", None) or {}
        tin, tout = int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0)
        price = PRICES_PER_1M.get(model)
        cost = (tin * price[0] + tout * price[1]) / 1e6 if price else None
        with self._lock:
            a = self.by_agent.setdefault(agent, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            a["calls"] += 1
            a["input_tokens"] += tin
            a["output_tokens"] += tout
            a["cost_usd"] += cost or 0.0
        return {"input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout, "cost_usd": cost}

    def summary(self) -> dict:
        with self._lock:
            total = {k: sum(a[k] for a in self.by_agent.values())
                     for k in ("calls", "input_tokens", "output_tokens", "cost_usd")}
            return {"total": total, "by_agent": {k: dict(v) for k, v in self.by_agent.items()}}


class _UsageProxy:
    """``USAGE`` resolves to the current session's meter, or the process-wide one."""

    def __init__(self):
        self.default = UsageMeter()

    def _meter(self) -> UsageMeter:
        return _SESSION_USAGE.get() or self.default

    def record(self, agent: str, model: str, msg: Any) -> dict:
        return self._meter().record(agent, model, msg)

    def summary(self) -> dict:
        return self._meter().summary()


USAGE = _UsageProxy()


def message_text(msg: Any) -> str:
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content blocks
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def parse_json_object(text: str) -> dict:
    """First top-level JSON object in a model reply (tolerates prose / code fences)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in reply: {text[:200]!r}")
    return json.loads(text[start:end + 1])
