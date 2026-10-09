"""FastAPI app: WebSocket bridge between the React console and one plant session per visitor.

    acetone-plant serve                 # http://localhost:8001
    acetone-plant serve --hosted        # public deployment: visitors bring their own OpenAI key

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
                    {"type": "set_api_key", "key": "sk-...", "model": "gpt-5-nano"}
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

from acetone_plant import __version__
from acetone_plant.agents.llm import is_openai_model

from .plant_service import jsonable
from .sessions import VISITOR_PRESETS, Session, SessionManager, check_openai_key


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
    print(f"Acetone plant {__version__}: {'hosted (visitor keys only)' if s.hosted else 'local'} mode, "
          f"up to {s.max_sessions} sessions, default model {s.model}", flush=True)
    yield
    await manager.stop()


app = FastAPI(title="Acetone plant console", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
async def health():
    return {"ok": True, "version": __version__, **manager.stats()}


@app.get("/api/config")
async def config():
    return {"version": __version__, **manager.settings.public()}


async def _set_key(session: Session, msg: dict) -> dict:
    key = str(msg.get("key") or "").strip()
    model = str(msg.get("model") or session.model)
    if model not in manager.settings.models and manager.settings.hosted:
        return {"ok": False, "summary": f"model {model!r} is not offered here"}
    if not is_openai_model(model):
        return {"ok": False, "summary": "only OpenAI models can be used with an API key"}
    if not key.startswith("sk-") or len(key) < 20:
        return {"ok": False, "summary": "that does not look like an OpenAI API key"}
    ok, reason = await check_openai_key(key)
    if not ok:
        return {"ok": False, "summary": reason}
    warning = await session.configure_agents(api_key=key, model=model)
    await session.service.broadcast({"type": "reset", "hello": session.service.hello()})
    return {"ok": not warning, "summary": warning or f"LLM agents on ({model}, your key)"}


async def _set_preset(session: Session, msg: dict) -> dict:
    preset = str(msg.get("preset") or "")
    if preset not in VISITOR_PRESETS:
        return {"ok": False, "summary": f"unknown agent configuration {preset!r}"}
    if preset.startswith("llm_admit"):
        from acetone_plant.admit import available
        ok, reason = available()
        if not ok:
            return {"ok": False, "summary": reason}
    warning = await session.configure_agents(preset=preset)
    await session.service.broadcast({"type": "reset", "hello": session.service.hello()})
    return {"ok": not warning, "summary": warning or f"agents: {VISITOR_PRESETS[preset]}"}


async def _clear_key(session: Session) -> dict:
    warning = await session.configure_agents(api_key="")
    await session.service.broadcast({"type": "reset", "hello": session.service.hello()})
    return {"ok": True, "summary": "key removed; " + (warning or "agents use the server configuration")}


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
            if kind == "chat":
                task = asyncio.create_task(answer(msg))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                continue
            if kind == "set_api_key":
                ack = await _set_key(session, msg)
            elif kind == "clear_api_key":
                ack = await _clear_key(session)
            elif kind == "set_agent_preset":
                ack = await _set_preset(session, msg)
            else:
                ack = await service.handle_command(msg)
            push({"type": "ack", "request": kind, **ack})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sender.cancel()
        for task in tasks:
            task.cancel()
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
