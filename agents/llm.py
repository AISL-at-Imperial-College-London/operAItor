"""Chat-model factory shared by the planning, action and chat agents.

OpenAI is the default provider. Model names starting with ``gpt`` / ``o<digit>``
use langchain-openai (key from OPENAI_API_KEY, or a ``.env`` file in the repo
root); any other name is served by a local Ollama instance (langchain-ollama),
the same convention as ctrl-alt-recover's cstr_case.py.

Local models (environment variables, read at call time):
  PLANT_LLM_BASE_URL   any OpenAI-compatible server for ANY model name, e.g.
                       http://localhost:1234/v1 (LM Studio), http://localhost:8000/v1 (vLLM),
                       http://localhost:8080/v1 (llama.cpp), http://localhost:11434/v1 (Ollama)
  PLANT_LLM_API_KEY    key for that server (default "local")
  PLANT_LLM_JSON_MODE  0 to skip response_format=json_object (some local servers reject it)
  PLANT_OLLAMA_URL     native Ollama endpoint (default http://localhost:11434)
  PLANT_LLM_MAX_RETRIES  retries on rate limits / timeouts (default 2)

DEFAULT_MODEL is the cheapest OpenAI text model on the pricing page when this
was written (gpt-5-nano, $0.05 / $0.40 per 1M input / output tokens,
https://developers.openai.com/api/docs/pricing, checked 2026-10-05). GPT-5 /
o-series models are reasoning models: they only accept temperature=1 and take a
``reasoning_effort`` (default "low" here, to keep control-loop latency
down; override with PLANT_REASONING_EFFORT).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
from typing import Any

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

_REPO_ROOT = Path(__file__).resolve().parent.parent
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


def is_openai_model(model: str) -> bool:
    return model.startswith("gpt") or bool(re.match(r"o\d", model))


def is_reasoning_model(model: str) -> bool:
    return bool(re.match(r"(gpt-5|gpt-6|o\d)", model))


def llm_status(model: str = DEFAULT_MODEL) -> tuple[bool, str]:
    """Can ``model`` be used right now? Returns (ok, human-readable reason)."""
    load_env()
    if os.environ.get("PLANT_LLM_BASE_URL"):
        try:
            import langchain_openai  # noqa: F401
        except ImportError:
            return False, "langchain-openai is not installed (py -3 -m pip install langchain-openai)"
        return True, f"{model} at {os.environ['PLANT_LLM_BASE_URL']}"
    if is_openai_model(model):
        try:
            import langchain_openai  # noqa: F401
        except ImportError:
            return False, "langchain-openai is not installed (py -3 -m pip install langchain-openai)"
        if not os.environ.get("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY is not set (put OPENAI_API_KEY=... in .env in the repo root)"
        return True, f"OpenAI {model}"
    try:
        import langchain_ollama  # noqa: F401
    except ImportError:
        return False, "langchain-ollama is not installed (py -3 -m pip install langchain-ollama)"
    return True, f"Ollama {model}"


def make_llm(model: str = DEFAULT_MODEL, timeout: float = 120.0, temperature: float = 0.0,
             json_mode: bool = False, **extra: Any):
    """Build a LangChain chat model. ``json_mode`` asks OpenAI for a JSON object reply."""
    load_env()
    retries = int(os.environ.get("PLANT_LLM_MAX_RETRIES", "2"))
    try:
        base_url = os.environ.get("PLANT_LLM_BASE_URL")
        if base_url:  # local OpenAI-compatible server (LM Studio, vLLM, llama.cpp, Ollama /v1)
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model=model, base_url=base_url, api_key=os.environ.get("PLANT_LLM_API_KEY", "local"),
                             timeout=timeout, max_retries=retries, temperature=temperature, **extra)
            use_json = json_mode and os.environ.get("PLANT_LLM_JSON_MODE", "1") != "0"
            return llm.bind(response_format={"type": "json_object"}) if use_json else llm
        if is_openai_model(model):
            from langchain_openai import ChatOpenAI
            kw: dict[str, Any] = dict(model=model, timeout=timeout, max_retries=retries, **extra)
            if is_reasoning_model(model):
                # GPT-5 / o-series accept only the default temperature (1). Older langchain-openai
                # releases (e.g. 0.2.x) send 0.7 unless told otherwise, which the API rejects.
                kw["temperature"] = 1
                kw["reasoning_effort"] = os.environ.get("PLANT_REASONING_EFFORT", "low")
            else:
                kw["temperature"] = temperature
            llm = ChatOpenAI(**kw)
            return llm.bind(response_format={"type": "json_object"}) if json_mode else llm
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, temperature=temperature, timeout=timeout,
                          base_url=os.environ.get("PLANT_OLLAMA_URL", "http://localhost:11434"),
                          **({"format": "json"} if json_mode else {}), **extra)
    except ImportError as exc:
        pkg = "langchain-openai" if is_openai_model(model) else "langchain-ollama"
        raise RuntimeError(f"model {model!r} needs `{pkg}`: py -3 -m pip install {pkg}") from exc


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


USAGE = UsageMeter()


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
