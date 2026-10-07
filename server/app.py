"""FastAPI + WebSocket bridge between the React console and the live plant.

    py -3 run_server.py                     # http://localhost:8001; OpenAI gpt-5-nano if a key is available
    py -3 run_server.py --llm off            # rule agents + offline chat, no API calls

Configuration comes from environment variables set by run_server.py:
PLANT_LLM (auto | on | off), PLANT_AGENT (preset; default llm when the LLM is on,
else rule), PLANT_LLM_MODEL (planning/action), PLANT_CHAT_MODEL (chat).

WebSocket ``/ws`` protocol (JSON):
  server -> client  {"type": "hello", ...}      full state on connect
                    {"type": "snapshot", ...}   every plant tick (0.5 sim-min)
                    {"type": "monitor", ...}    monitoring-agent result per cycle
                    {"type": "agent", "stage": "kg|plan|proposal|invalid|twin|applied|pending|advisory|rejected|..."}
                    {"type": "campaign", ...}   fault-campaign progress
                    {"type": "chat", ...}       chat-agent reply (to the asking client only)
                    {"type": "status"|"message"|"reset"|"agent_mode"|"ack", ...}
  client -> server  {"type": "load"|"run"|"pause"|"step"|"speed"|"action"|"inject"|
                     "clear_injections"|"ack_all"|"reset_sif"|"agent_mode"|
                     "agent_approve"|"agent_dismiss"|"campaign_start"|"campaign_stop"|"chat", ...}
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from agents.chat import OfflineChat
from agents.kg import KnowledgeGraph
from agents.llm import DEFAULT_MODEL, llm_status
from .plant_service import PlantService, jsonable

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT / "frontend" / "dist"
LLM_MODE = os.environ.get("PLANT_LLM", "auto")
LLM_MODEL = os.environ.get("PLANT_LLM_MODEL") or DEFAULT_MODEL
CHAT_MODEL = os.environ.get("PLANT_CHAT_MODEL") or LLM_MODEL
AGENT_PRESET = os.environ.get("PLANT_AGENT", "")

service = PlantService()
kg = KnowledgeGraph()
supervisor = None
chat = None


def _llm_enabled() -> tuple[bool, str]:
    if LLM_MODE == "off":
        return False, "LLM disabled (--llm off)"
    ok, reason = llm_status(LLM_MODEL)
    if not ok and LLM_MODE == "on":
        raise SystemExit(f"--llm on, but {reason}")
    return ok, reason


def _start_agents() -> str:
    """Start the LangGraph supervisor and chat agent; return a warning ('' if all fine)."""
    global supervisor, chat
    try:  # the agents need langgraph; the plant console works without it
        from agents.chat import ChatAgent
        from agents.recovery_graph import make_config
        from agents.supervisor import AsyncSupervisor
    except ImportError as exc:
        chat = OfflineChat(service, kg)
        return f"{exc}. Install with: py -3 -m pip install \"langgraph>=1.0,<2\""
    use_llm, reason = _llm_enabled()
    # Every action comes from the LLM. Without one the agents only monitor (PCA detection, no actions).
    preset = AGENT_PRESET or ("llm" if use_llm else "passive")
    warning = "" if use_llm else f"no LLM ({reason}); agents monitor only (no actions) and the chat is offline"
    try:
        diag = os.environ.get("PLANT_DIAGNOSER") or ("llm" if use_llm else "heuristic")
        supervisor = AsyncSupervisor(service, make_config(preset, llm_model=LLM_MODEL, diagnoser=diag), kg=kg)
    except Exception as exc:  # e.g. LLM preset without its provider package / API key
        warning = f"agent preset {preset!r} failed ({exc}); agents monitor only (no actions)"
        supervisor = AsyncSupervisor(service, make_config("passive"), kg=kg)
    supervisor.start()
    chat = ChatAgent(service, kg, llm_model=CHAT_MODEL if use_llm else None, agent_info=supervisor.info)
    if chat.llm_error:
        warning = (warning + "; " if warning else "") + f"chat model unavailable ({chat.llm_error}), using offline chat"
    llm_txt = supervisor.info()["llm_model"] or "none (rules)"
    chat_txt = f"llm {CHAT_MODEL}" if chat.mode == "llm" else "offline"
    print(f"Agents: preset {supervisor.cfg.name}, planning/action model {llm_txt}, chat {chat_txt}", flush=True)
    return warning


@asynccontextmanager
async def lifespan(app: FastAPI):
    await service.start()
    service.hello_extras.append(lambda: {"chat": {
        "mode": chat.mode if chat else "offline",
        "model": getattr(chat, "model", None) if chat and chat.mode == "llm" else None}})
    warning = _start_agents()
    if warning:
        print(f"WARNING: {warning}", flush=True)
        await service.notify(f"Agents: {warning}", "warn")
    yield
    await service.stop()


app = FastAPI(title="Acetone plant console", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/state")
async def state():
    return service.hello()


@app.post("/api/command")
async def command(msg: dict):
    if msg.get("type") == "chat":
        return await chat.ask(str(msg.get("text", "")), str(msg.get("session", "api")))
    return await service.handle_command(msg)


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    queue = service.subscribe()
    await websocket.send_text(json.dumps({"type": "hello", **service.hello()}))

    def push(event: dict) -> None:  # this client only; drop the oldest message if it lags
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(jsonable(event))

    async def pump_out():
        while True:
            await websocket.send_text(json.dumps(await queue.get()))

    async def answer(msg: dict):
        # Runs as its own task so a slow LLM never blocks this client's other commands.
        session = str(msg.get("session") or id(websocket))
        reply = await chat.ask(str(msg.get("text", "")), session)
        push({"type": "chat", "id": msg.get("id"), "role": "assistant", **reply})
        if reply.get("mode") == "llm":
            from agents.llm import USAGE
            push({"type": "llm_usage", **USAGE.summary()})

    sender = asyncio.create_task(pump_out())
    tasks: set[asyncio.Task] = set()
    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            if msg.get("type") == "chat":
                task = asyncio.create_task(answer(msg))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                continue
            ack = await service.handle_command(msg)
            push({"type": "ack", "request": msg.get("type"), **ack})
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        for task in tasks:
            task.cancel()
        service.unsubscribe(queue)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
if (FRONTEND_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    @app.get("/", response_class=HTMLResponse)
    async def frontend_missing():
        return ("<h2>Plant server is running, but the console is not built.</h2>"
                "<p>Run <code>cd frontend; npm install; npm run build</code> and reload, "
                "or use <code>npm run dev</code> and open http://localhost:5173.</p>")
