"""Per-visitor sessions: each browser session gets its own plant, agents, chat and LLM meter.

A session is created on the first WebSocket connection and reattached when the same browser
tab reconnects (the console keeps the session id in sessionStorage). Sessions without a
connected client are closed after ``idle_min``; the number of live sessions is capped so a
small free host is not overloaded.

LLM connections are per session. A visitor connects a provider (Groq / Gemini / OpenRouter free
tiers, their own Ollama URL, or OpenAI; see ``agents/providers.py``). The key is kept in this
process's memory for the session only, bound to the session's agents through context variables
(``agents.llm.use_session_llm``), never logged, never written to disk and never sent back to the
browser. Locally, with no connection, the server's own OpenAI key or a local Ollama is used.
"""
from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass, field
import os
import secrets
import time

from operaitor.agents.chat import OfflineChat
from operaitor.agents.kg import KnowledgeGraph
from operaitor.agents.llm import DEFAULT_MODEL, UsageMeter, hosted, llm_status, load_env, use_session_llm
from operaitor.agents.providers import PROVIDERS, Endpoint, local_ollama_model
from operaitor.fdd import DEFAULT_MODEL_PATH, PCAMonitor

from .plant_service import PlantService


def default_model() -> str:
    """Free first: PLANT_LLM_MODEL if set; OpenAI only when a key is configured; else local Ollama."""
    load_env()
    if os.environ.get("PLANT_LLM_MODEL"):
        return os.environ["PLANT_LLM_MODEL"]
    if hosted() or os.environ.get("OPENAI_API_KEY"):
        return DEFAULT_MODEL
    return local_ollama_model() or DEFAULT_MODEL


def provider_catalog() -> dict:
    """What the console may offer (no secrets)."""
    return {k: {"label": v["label"], "models": v["models"], "free": v["free"], "key": v["key"],
                "signup": v["signup"], "url": k == "ollama"} for k, v in PROVIDERS.items()}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    """Server configuration from environment variables (see docs/deployment.md)."""
    hosted: bool = field(default_factory=hosted)
    llm_mode: str = field(default_factory=lambda: os.environ.get("PLANT_LLM", "auto"))
    preset: str = field(default_factory=lambda: os.environ.get("PLANT_AGENT", ""))
    model: str = field(default_factory=default_model)
    chat_model: str = field(default_factory=lambda: os.environ.get("PLANT_CHAT_MODEL", ""))
    diagnoser: str = field(default_factory=lambda: os.environ.get("PLANT_DIAGNOSER", ""))
    max_sessions: int = field(default_factory=lambda: int(_env_float("PLANT_MAX_SESSIONS", 4)))
    idle_min: float = field(default_factory=lambda: _env_float("PLANT_SESSION_IDLE_MIN", 15))
    max_age_min: float = field(default_factory=lambda: _env_float("PLANT_SESSION_MAX_MIN", 180))
    models: tuple[str, ...] = field(default_factory=lambda: tuple(
        m.strip() for m in os.environ.get("PLANT_ALLOWED_MODELS", "gpt-5-nano,gpt-5-mini").split(",") if m.strip()))

    def public(self) -> dict:
        return {"hosted": self.hosted, "models": list(self.models), "default_model": self.model,
                "max_sessions": self.max_sessions, "idle_min": self.idle_min, "providers": provider_catalog()}


# Agent configurations a visitor can choose in the console (label shown in the UI): the reference pipelines.
from operaitor.agents.pipeline import REFERENCE_PIPELINES as VISITOR_PRESETS  # noqa: E402


def default_preset() -> str:
    """ADMIT validation by default when admit-bench is installed, else the digital twin."""
    from operaitor.admit import available
    return "llm_admit" if available()[0] else "llm"


class Session:
    def __init__(self, sid: str, settings: Settings, kg: KnowledgeGraph, pca: PCAMonitor | None):
        self.id = sid
        self.settings = settings
        self.kg = kg               # the knowledge graph this session's agents use
        self.reference_kg = kg     # the shared reference graph (a custom pipeline may bring its own)
        self.pipeline = None       # custom pipeline (agents/pipeline.py PipelineSpec); None = the reference preset
        self.plugin = None         # its Python agents, loaded (operaitor/plugins.py); own computer only
        self.bench = None          # pipeline benchmark (server/pipeline_bench.py)
        self.kg_report: dict | None = None
        self.pca = pca
        self.service = PlantService()
        self.usage = UsageMeter()
        self.supervisor = None
        self.chat = None
        self.model = settings.model
        self.preset = settings.preset
        self.endpoint: Endpoint | None = None  # visitor-connected LLM (holds the key; never sent out)
        # The visitor's browser makes this session's LLM calls when they connect a provider (browser_llm.py).
        from operaitor.agents.browser_llm import BrowserRelay
        self.relay = BrowserRelay()
        self.llm_reason = ""
        self.llm_ctx = contextvars.copy_context()
        self.clients = 0
        self.closed = False
        self.created = time.monotonic()
        self.last_seen = self.created
        self.service.hello_extras.append(self._hello)

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await self.service.start()
        await self.configure_agents()

    async def close(self, reason: str = "") -> None:
        self.closed = True
        if self.bench is not None and self.bench.task is not None:
            self.bench.stop()
            self.bench.task.cancel()
        if reason:
            await self.service.broadcast({"type": "expired", "text": reason})
        if self.supervisor is not None:
            await self.supervisor.stop()
        await self.service.stop()
        self.endpoint = None
        self.relay.fail_all("session closed")

    @property
    def key_source(self) -> str | None:
        """'visitor' = connected in the console; 'server' = this server's own configuration."""
        if self.endpoint is not None:
            return "visitor"
        if not self.settings.hosted and self.chat is not None and self.chat.mode == "llm":
            return "server"
        return None

    def connection(self) -> dict | None:
        if self.endpoint is not None:
            return self.endpoint.public()
        if self.key_source == "server":
            free = not self.model.startswith("gpt")
            return {"provider": "ollama" if free else "openai",
                    "label": "Ollama on this machine" if free else "OpenAI (server key)",
                    "model": self.model, "free": free, "url": None}
        return None

    async def configure_agents(self, endpoint: Endpoint | None = None, clear: bool = False,
                               model: str | None = None, preset: str | None = None,
                               pipeline=None, kg=None, kg_report: dict | None = None, plugin=None) -> str:
        """(Re)build supervisor + chat with this session's LLM connection; returns a warning or ''.

        ``preset``: run that reference pipeline. ``pipeline``: run a custom pipeline (with ``kg``, its
        parsed knowledge graph, if it brings one)."""
        if clear:
            self.endpoint, self.model = None, self.settings.model
        if endpoint is not None:
            self.endpoint, self.model = endpoint, endpoint.model
        if model:
            self.model = model
        if preset is not None:
            self.preset, self.pipeline, self.plugin = preset, None, None
            self.kg, self.kg_report = self.reference_kg, None
        if pipeline is not None:
            self.preset, self.pipeline = pipeline.base, (None if pipeline.reference else pipeline)
            self.plugin = plugin if pipeline.code else None
            self.kg, self.kg_report = (kg, kg_report) if kg is not None else (self.reference_kg, None)
        if self.supervisor is not None:
            await self.supervisor.stop()
            self.supervisor = None
        self.llm_ctx = contextvars.copy_context()
        self.llm_ctx.run(use_session_llm, self.endpoint, self.usage)
        return self.llm_ctx.run(self._build_agents)

    def _build_agents(self) -> str:
        s = self.settings
        try:  # the agents need langgraph; the plant console works without it
            from operaitor.agents.chat import ChatAgent
            from operaitor.agents.recovery_graph import make_config
            from operaitor.agents.supervisor import AsyncSupervisor
        except ImportError as exc:
            self.chat = OfflineChat(self.service, self.kg)
            self.llm_reason = f"agents unavailable ({exc})"
            return self.llm_reason
        if s.llm_mode == "off":
            use_llm, reason = False, "LLM disabled on this server"
        else:
            use_llm, reason = llm_status(self.model)
        self.llm_reason = "" if use_llm else reason
        preset = (self.preset or default_preset()) if use_llm else "passive"
        diag = s.diagnoser or ("llm" if use_llm else "heuristic")
        warning = "" if use_llm else f"no LLM ({reason}); agents monitor only (no actions), chat is offline"
        try:
            plugin, custom_validator = None, False
            if use_llm and self.pipeline is not None:
                from operaitor.agents.pipeline import config_for
                cfg = config_for(self.pipeline, llm_model=self.model, diagnoser=diag)
                prompts = self.pipeline.prompts
                plugin, custom_validator = self.plugin, self.pipeline.settings.get("validator") == "custom"
            else:
                cfg, prompts = make_config(preset, llm_model=self.model, diagnoser=diag), {}
                if self.pipeline is not None:  # monitor only (no LLM): the pipeline's own detectors still watch
                    plugin = self.plugin
            self.supervisor = AsyncSupervisor(self.service, cfg, kg=self.kg, pca=self.pca, prompts=prompts,
                                              plugin=plugin, custom_validator=custom_validator)
        except Exception as exc:  # e.g. preset needs a package that is missing
            warning = f"agent preset {preset!r} failed ({exc}); agents monitor only"
            self.supervisor = AsyncSupervisor(self.service, make_config("passive"), kg=self.kg, pca=self.pca)
        self.supervisor.start()  # task created inside llm_ctx -> keeps this session's key and meter
        chat_model = (s.chat_model if self.endpoint is None else "") or self.model
        self.chat = ChatAgent(self.service, self.kg, llm_model=chat_model if use_llm else None,
                              agent_info=self.supervisor.info)
        if self.chat.llm_error:
            warning = (warning + "; " if warning else "") + f"chat model unavailable ({self.chat.llm_error})"
        return warning

    async def ask_chat(self, text: str, thread: str) -> dict:
        task = asyncio.create_task(self.chat.ask(text, thread), context=self.llm_ctx)
        return await task

    def usage_summary(self) -> dict:
        return self.usage.summary()

    def pipeline_info(self) -> dict:
        """The pipeline this session runs: a reference one, or the visitor's custom one (no prompt / KG text)."""
        from operaitor.agents.pipeline import reference_spec
        if self.pipeline is not None:
            return self.pipeline.public(self.kg_report)
        return reference_spec(self.preset or default_preset()).public()

    def _hello(self) -> dict:
        from operaitor.plugins import plugins_allowed
        allowed, why = plugins_allowed(self.settings.hosted)
        return {"session": {"id": self.id, "hosted": self.settings.hosted, "models": list(self.settings.models),
                            "plugins": {"allowed": allowed, "reason": why},
                            "desktop": os.environ.get("OPERAITOR_DESKTOP") == "1",  # the desktop app: Quit offered
                            "bench": self.bench.status() if self.bench is not None else None,
                            "llm": {"model": self.model, "key_source": self.key_source,
                                    "connection": self.connection(), "providers": provider_catalog(),
                                    "active": bool(self.chat is not None and self.chat.mode == "llm"),
                                    "reason": self.llm_reason, "preset": self.preset or default_preset(),
                                    "presets": VISITOR_PRESETS},
                            "pipeline": self.pipeline_info()},
                "chat": {"mode": self.chat.mode if self.chat else "offline",
                         "model": getattr(self.chat, "model", None) if self.chat and self.chat.mode == "llm" else None}}


class SessionManager:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.kg = KnowledgeGraph()
        self.pca = PCAMonitor.load(DEFAULT_MODEL_PATH) if os.path.exists(DEFAULT_MODEL_PATH) else None
        self.sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    async def start(self) -> None:
        self._reaper = asyncio.create_task(self._reap_loop(), name="session-reaper")

    async def stop(self) -> None:
        if self._reaper:
            self._reaper.cancel()
        for s in list(self.sessions.values()):
            await s.close()
        self.sessions.clear()

    async def attach(self, sid: str | None) -> Session | None:
        """Reattach to a live session or create one; None when the server is full."""
        async with self._lock:
            if sid and sid in self.sessions:
                s = self.sessions[sid]
            else:
                if len(self.sessions) >= self.settings.max_sessions:
                    await self._reap(force_idle=True)
                if len(self.sessions) >= self.settings.max_sessions:
                    return None
                s = Session(secrets.token_urlsafe(12), self.settings, self.kg, self.pca)
                await s.start()
                self.sessions[s.id] = s
            s.clients += 1
            s.last_seen = time.monotonic()
            return s

    def detach(self, s: Session) -> None:
        s.clients = max(0, s.clients - 1)
        s.last_seen = time.monotonic()

    async def _reap(self, force_idle: bool = False) -> None:
        now = time.monotonic()
        for sid, s in list(self.sessions.items()):
            idle_s = now - s.last_seen
            idle = s.clients == 0 and (idle_s > 60 * self.settings.idle_min or (force_idle and idle_s > 60))
            too_old = self.settings.hosted and now - s.created > 60 * self.settings.max_age_min
            if idle or too_old:
                self.sessions.pop(sid, None)
                await s.close("Session ended (time limit reached). Reload the page for a new plant." if too_old else "")

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            async with self._lock:
                await self._reap()

    def stats(self) -> dict:
        return {"sessions": len(self.sessions), "max_sessions": self.settings.max_sessions,
                "connected": sum(s.clients for s in self.sessions.values())}
