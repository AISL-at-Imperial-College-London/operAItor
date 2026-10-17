"""FastAPI app: WebSocket bridge between the React console and one plant session per visitor.

    operaitor serve                 # http://localhost:8001
    operaitor serve --hosted        # public deployment: visitors bring their own OpenAI key

HTTP
  GET  /api/health          liveness + session counts (for the host's health check)
  GET  /api/config          public settings (hosted?, allowed models)
  GET  /                    the built console (frontend/dist, or PLANT_FRONTEND_DIST)

WebSocket ``/ws?session=<id>`` (JSON). Reconnecting with the same id reattaches to that plant.
  server -> client  {"type": "hello", "session": {...}, ...}   full state on connect
                    {"type": "snapshot"|"monitor"|"agent"|"campaign"|"chat"|"llm_usage"|
                     "status"|"message"|"reset"|"agent_mode"|"ack", ...}
                    {"type": "busy", "text"}      server full, connection closed
                    {"type": "expired", "text"}   session closed by the server
  client -> server  {"type": "load"|"run"|"pause"|"step"|"speed"|"pace"|"action"|"inject"|
                     "clear_injections"|"ack_all"|"reset_sif"|"agent_mode"|"agent_approve"|
                     "agent_dismiss"|"diagnose_now"|"campaign_start"|"campaign_stop"|"chat", ...}
                    {"type": "set_llm", "provider": "groq|gemini|openrouter|ollama|openai",
                     "model": "...", "key": "...", "url": "https://... (ollama only)"}
                    {"type": "clear_api_key"}
                    {"type": "set_agent_preset", "preset": "llm" | "llm_admit" | "llm_judge" | "llm_lucky"}
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from operaitor import __version__
from operaitor.agents.llm import is_openai_model

from .plant_service import jsonable
from .sessions import VISITOR_PRESETS, Session, SessionManager


def _frontend_dist() -> Path:
    if os.environ.get("PLANT_FRONTEND_DIST"):
        return Path(os.environ["PLANT_FRONTEND_DIST"])
    bundled = Path(__file__).resolve().parent / "web"  # copied in by the Docker build / wheel build
    return bundled if (bundled / "index.html").exists() else Path.cwd() / "frontend" / "dist"


manager = SessionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.start()
    s = manager.settings
    print(f"OperAItor {__version__}: {'hosted (visitor keys only)' if s.hosted else 'local'} mode, "
          f"up to {s.max_sessions} sessions, default model {s.model}", flush=True)
    yield
    await manager.stop()


app = FastAPI(title="OperAItor", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
async def health():
    return {"ok": True, "version": __version__, **manager.stats()}


@app.get("/api/config")
async def config():
    return {"version": __version__, **manager.settings.public()}


async def _set_llm(session: Session, msg: dict) -> dict:
    """Connect this session's agents to an LLM provider.

    ``in_browser`` (the public console): the visitor's browser has already checked its key with the
    provider and makes every call itself; no key is sent here. A public server refuses keys outright,
    so a visitor's key cannot reach it. Otherwise (a private server) the key is held in memory."""
    from operaitor.agents.providers import PROVIDERS, Endpoint, check_endpoint, endpoint_for
    provider = str(msg.get("provider") or "openai")
    if provider not in PROVIDERS:
        return {"ok": False, "summary": f"unknown provider {provider!r}"}
    p = PROVIDERS[provider]
    model = str(msg.get("model") or p["models"][0]).strip()
    key = str(msg.get("key") or "").strip()
    url = str(msg.get("url") or "").strip()
    if not model or len(model) > 120 or len(key) > 400 or len(url) > 300:
        return {"ok": False, "summary": "invalid model, key or address"}
    if msg.get("in_browser"):
        if key:
            return {"ok": False, "summary": "send no key: the browser keeps it and calls the provider itself"}
        base = endpoint_for(provider, model, "", url or "http://localhost:11434")
        ep = Endpoint(provider, base.base_url, "", model, base.json_mode, relay=session.relay)
        warning = await session.configure_agents(endpoint=ep)
        await session.service.broadcast({"type": "reset", "hello": session.service.hello()})
        return {"ok": not warning, "summary": warning or f"LLM agents on: {p['label']}, {model} (calls made by your browser)"}
    if manager.settings.hosted:
        return {"ok": False, "summary": "this server never receives API keys; reload the page to use the current console"}
    if provider == "openai" and model not in manager.settings.models:
        return {"ok": False, "summary": f"model {model!r} is not offered here"}
    if provider == "ollama" and not url and manager.settings.hosted:
        return {"ok": False, "summary": "give the https address of your Ollama server"}
    ep = endpoint_for(provider, model, key, url)
    ok, reason = await check_endpoint(ep, public=manager.settings.hosted)
    if not ok:
        return {"ok": False, "summary": reason}
    warning = await session.configure_agents(endpoint=ep)
    await session.service.broadcast({"type": "reset", "hello": session.service.hello()})
    return {"ok": not warning, "summary": warning or f"LLM agents on: {p['label']}, {model}"}


def _server_ollama(session: Session) -> bool:
    """The session uses this machine's own Ollama (a private / desktop server, nothing connected by the visitor)."""
    conn = session.connection() or {}
    return not manager.settings.hosted and session.key_source == "server" and conn.get("provider") == "ollama"


async def _list_models(session: Session) -> dict:
    """Models the console may offer for the server's own Ollama (visitor connections list theirs in the browser)."""
    from operaitor.agents.providers import local_ollama_models
    models = await asyncio.to_thread(local_ollama_models) if _server_ollama(session) else []
    return {"ok": True, "summary": f"{len(models)} models", "models": models}


async def _set_model(session: Session, msg: dict) -> dict:
    """Switch the model of the server's own Ollama for this session (visitor connections reconnect in the browser)."""
    from operaitor.agents.providers import local_ollama_models
    model = str(msg.get("model") or "").strip()
    if not _server_ollama(session):
        return {"ok": False, "summary": "only the Ollama on this machine is switched here; your own connection changes in the browser"}
    if not model or len(model) > 120:
        return {"ok": False, "summary": "invalid model name"}
    names = await asyncio.to_thread(local_ollama_models)
    if model not in names and f"{model}:latest" not in names:
        return {"ok": False, "summary": f"Ollama on this machine has no model {model!r}" + (f"; it has {', '.join(names[:8])}" if names else "")}
    warning = await session.configure_agents(model=model)
    await session.service.broadcast({"type": "reset", "hello": session.service.hello()})
    return {"ok": not warning, "summary": warning or f"agents now use {model} (Ollama on this machine)"}


async def _set_key(session: Session, msg: dict) -> dict:  # older consoles: OpenAI key only
    return await _set_llm(session, {**msg, "provider": "openai"})


async def _set_preset(session: Session, msg: dict) -> dict:
    preset = str(msg.get("preset") or "")
    if preset not in VISITOR_PRESETS:
        return {"ok": False, "summary": f"unknown agent configuration {preset!r}"}
    if preset.startswith("llm_admit"):
        from operaitor.admit import available
        ok, reason = available()
        if not ok:
            return {"ok": False, "summary": reason}
    warning = await session.configure_agents(preset=preset)
    await session.service.broadcast({"type": "reset", "hello": session.service.hello()})
    return {"ok": not warning, "summary": warning or f"agents: {VISITOR_PRESETS[preset]}"}


def _pipeline_defaults() -> dict:
    """What the console's pipeline editor starts from: the reference pipelines, prompts and settings."""
    from operaitor.agents import pipeline as pl
    return {"ok": True, "summary": "reference pipelines",
            "references": {k: {"label": label, "settings": pl.reference_settings(k)}
                           for k, label in pl.REFERENCE_PIPELINES.items()},
            "prompts": pl.reference_prompts(), "prompt_agents": pl.PROMPT_AGENTS, "settings": pl.SETTINGS,
            "limits": {"prompt_chars": pl.MAX_PROMPT_CHARS, "kg_bytes": pl.MAX_KG_BYTES}}


async def _parse_kg(text: str) -> tuple[object | None, dict | None, str]:
    """Parse a custom knowledge graph off the event loop; returns (kg, report, error)."""
    from operaitor.agents.pipeline import MAX_KG_BYTES, kg_report, load_kg
    if len(text.encode()) > MAX_KG_BYTES:
        return None, None, f"the knowledge graph is larger than {MAX_KG_BYTES // 1_000_000} MB"
    try:
        kg = await asyncio.to_thread(load_kg, text)
        return kg, await asyncio.to_thread(kg_report, kg, manager.kg), ""
    except ValueError as exc:
        return None, None, f"knowledge graph not loaded: {exc}"


async def _check_kg(msg: dict) -> dict:
    """Check an uploaded knowledge graph without running it."""
    _, report, error = await _parse_kg(str(msg.get("kg") or ""))
    if error:
        return {"ok": False, "summary": error}
    return {"ok": True, "summary": f"{report['nodes']} nodes, {len(report['warnings'])} warnings", "report": report}


async def _set_pipeline(session: Session, msg: dict) -> dict:
    """Run a custom pipeline (data only: settings, prompts, knowledge graph) in this session."""
    from operaitor.agents.pipeline import parse_pipeline
    spec, errors = parse_pipeline(msg.get("pipeline"))
    if errors:
        return {"ok": False, "summary": "pipeline not valid: " + "; ".join(errors[:4]), "errors": errors}
    validator = spec.settings.get("validator") or ("admit" if spec.base.startswith("llm_admit") else "")
    if validator.startswith("admit"):
        from operaitor.admit import available
        ok, reason = available()
        if not ok:
            return {"ok": False, "summary": reason}
    kg = report = None
    if spec.kg:
        kg, report, error = await _parse_kg(spec.kg)
        if error:
            return {"ok": False, "summary": error}
    warning = await session.configure_agents(pipeline=spec, kg=kg, kg_report=report)
    await session.service.broadcast({"type": "reset", "hello": session.service.hello()})
    label = "reference pipeline" if spec.reference else "custom pipeline"
    return {"ok": not warning, "summary": warning or f"agents: {label} \"{spec.name}\" ({spec.fingerprint()})",
            **({"report": report} if report else {})}


async def _clear_key(session: Session) -> dict:
    warning = await session.configure_agents(clear=True)
    await session.service.broadcast({"type": "reset", "hello": session.service.hello()})
    return {"ok": True, "summary": "disconnected; " + (warning or "agents use the server configuration")}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    session = await manager.attach(websocket.query_params.get("session"))
    if session is None:
        await websocket.send_text(json.dumps({
            "type": "busy", "text": f"All {manager.settings.max_sessions} plant sessions are in use. "
                                    "Please try again in a few minutes."}))
        await websocket.close()
        return
    service = session.service
    queue = service.subscribe()
    await websocket.send_text(json.dumps({"type": "hello", **service.hello()}))

    def push(event: dict) -> None:  # this client only; drop the oldest message if it lags
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(jsonable(event))

    session.relay.attach(push)  # this tab can make the session's LLM calls (browser_llm.py)

    async def pump_out():
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event))
            if event.get("type") == "expired":
                await websocket.close()
                return

    async def answer(msg: dict):
        # Own task, so a slow LLM never blocks this client's other commands.
        reply = await session.ask_chat(str(msg.get("text", "")), str(msg.get("session") or "console"))
        push({"type": "chat", "id": msg.get("id"), "role": "assistant", **reply})
        if reply.get("mode") == "llm":
            push({"type": "llm_usage", **session.usage_summary()})

    sender = asyncio.create_task(pump_out())
    tasks: set[asyncio.Task] = set()
    try:
        while not session.closed:
            msg = json.loads(await websocket.receive_text())
            session.last_seen = time.monotonic()
            kind = msg.get("type")
            if kind == "llm_response":  # the browser's answer to an llm_request; no ack
                session.relay.resolve(msg)
                continue
            if kind == "chat":
                task = asyncio.create_task(answer(msg))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                continue
            if kind == "set_llm":
                ack = await _set_llm(session, msg)
            elif kind == "set_api_key":
                ack = await _set_key(session, msg)
            elif kind == "clear_api_key":
                ack = await _clear_key(session)
            elif kind == "set_agent_preset":
                ack = await _set_preset(session, msg)
            elif kind == "list_models":
                ack = await _list_models(session)
            elif kind == "set_model":
                ack = await _set_model(session, msg)
            elif kind == "pipeline_defaults":
                ack = _pipeline_defaults()
            elif kind == "check_kg":
                ack = await _check_kg(msg)
            elif kind == "set_pipeline":
                ack = await _set_pipeline(session, msg)
            else:
                ack = await service.handle_command(msg)
            push({"type": "ack", "request": kind, **ack})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sender.cancel()
        for task in tasks:
            task.cancel()
        session.relay.detach(push)
        service.unsubscribe(queue)
        manager.detach(session)


_DIST = _frontend_dist()
if (_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
else:
    @app.get("/", response_class=HTMLResponse)
    async def frontend_missing():
        return ("<h2>Plant server is running, but the console is not built.</h2>"
                "<p>Run <code>cd frontend; npm install; npm run build</code> and reload, "
                "or use <code>npm run dev</code> and open http://localhost:5173.</p>")
