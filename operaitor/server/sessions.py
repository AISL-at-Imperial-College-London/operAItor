"""Per-visitor sessions: each browser session gets its own plant, agents, chat and LLM meter.

A session is created on the first WebSocket connection and reattached when the same browser
tab reconnects (the console keeps the session id in sessionStorage). Sessions without a
connected client are closed after ``idle_min``; the number of live sessions is capped so a
small free host is not overloaded.

LLM credentials are per session. On the hosted app (``PLANT_HOSTED=1``) a visitor supplies
their own OpenAI key; it is kept in this process's memory for the session only, bound to the
session's agents through context variables (``agents.llm.use_session_llm``), never logged,
never written to disk and never sent back to the browser.
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
from operaitor.agents.llm import DEFAULT_MODEL, UsageMeter, hosted, llm_status, use_session_llm
from operaitor.fdd import DEFAULT_MODEL_PATH, PCAMonitor

from .plant_service import PlantService


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
    model: str = field(default_factory=lambda: os.environ.get("PLANT_LLM_MODEL") or DEFAULT_MODEL)
    chat_model: str = field(default_factory=lambda: os.environ.get("PLANT_CHAT_MODEL", ""))
    diagnoser: str = field(default_factory=lambda: os.environ.get("PLANT_DIAGNOSER", ""))
    max_sessions: int = field(default_factory=lambda: int(_env_float("PLANT_MAX_SESSIONS", 4)))
    idle_min: float = field(default_factory=lambda: _env_float("PLANT_SESSION_IDLE_MIN", 15))
    max_age_min: float = field(default_factory=lambda: _env_float("PLANT_SESSION_MAX_MIN", 180))
    models: tuple[str, ...] = field(default_factory=lambda: tuple(
        m.strip() for m in os.environ.get("PLANT_ALLOWED_MODELS", "gpt-5-nano,gpt-5-mini").split(",") if m.strip()))

    def public(self) -> dict:
        return {"hosted": self.hosted, "models": list(self.models), "default_model": self.model,
                "max_sessions": self.max_sessions, "idle_min": self.idle_min}


async def check_openai_key(key: str) -> tuple[bool, str]:
    """Cheap validity check (lists models; no tokens are spent)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        return False, f"could not reach OpenAI ({type(exc).__name__})"
    if r.status_code == 200:
        return True, "key accepted"
    if r.status_code == 401:
        return False, "OpenAI rejected this key (401)"
    return False, f"OpenAI answered {r.status_code}"


# Agent configurations a visitor can choose in the console (label shown in the UI).
VISITOR_PRESETS = {"llm_admit": "LLM + ADMIT gates", "llm_admit_nosim": "LLM + ADMIT (no simulation)",
                   "llm": "LLM + digital twin", "llm_judge": "LLM + LLM judge",
                   "llm_lucky": "LLM, no validation"}


def default_preset() -> str:
    """ADMIT validation by default when admit-bench is installed, else the digital twin."""
    from operaitor.admit import available
    return "llm_admit" if available()[0] else "llm"


class Session:
    def __init__(self, sid: str, settings: Settings, kg: KnowledgeGraph, pca: PCAMonitor | None):
        self.id = sid
        self.settings = settings
        self.kg = kg
        self.pca = pca
        self.service = PlantService()
        self.usage = UsageMeter()
        self.supervisor = None
        self.chat = None
        self.model = settings.model
        self.preset = settings.preset
        self._api_key: str | None = None
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
        if reason:
            await self.service.broadcast({"type": "expired", "text": reason})
        if self.supervisor is not None:
            await self.supervisor.stop()
        await self.service.stop()
        self._api_key = None

    @property
    def key_source(self) -> str | None:
        if self._api_key:
            return "visitor"
        if not self.settings.hosted and os.environ.get("OPENAI_API_KEY"):
            return "server"
        return None

    async def configure_agents(self, api_key: str | None = None, model: str | None = None,
                               preset: str | None = None) -> str:
        """(Re)build supervisor + chat with this session's key and model; returns a warning or ''."""
        if api_key is not None:
            self._api_key = api_key or None
        if model:
            self.model = model
        if preset is not None:
            self.preset = preset
        if self.supervisor is not None:
            await self.supervisor.stop()
            self.supervisor = None
        self.llm_ctx = contextvars.copy_context()
        self.llm_ctx.run(use_session_llm, self._api_key, self.usage)
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
            self.supervisor = AsyncSupervisor(self.service, make_config(preset, llm_model=self.model, diagnoser=diag),
                                              kg=self.kg, pca=self.pca)
        except Exception as exc:  # e.g. preset needs a package that is missing
            warning = f"agent preset {preset!r} failed ({exc}); agents monitor only"
            self.supervisor = AsyncSupervisor(self.service, make_config("passive"), kg=self.kg, pca=self.pca)
        self.supervisor.start()  # task created inside llm_ctx -> keeps this session's key and meter
        chat_model = s.chat_model or self.model
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

    def _hello(self) -> dict:
        return {"session": {"id": self.id, "hosted": self.settings.hosted, "models": list(self.settings.models),
                            "llm": {"model": self.model, "key_source": self.key_source,
                                    "active": bool(self.chat is not None and self.chat.mode == "llm"),
                                    "reason": self.llm_reason, "preset": self.preset or default_preset(),
                                    "presets": VISITOR_PRESETS}},
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
