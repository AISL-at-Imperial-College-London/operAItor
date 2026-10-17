"""The web server (FastAPI app + WebSocket protocol) end to end, in public mode, without network or LLM."""
from __future__ import annotations

import os
from unittest import mock

import pytest
from fastapi.testclient import TestClient

with mock.patch.dict(os.environ, {"PLANT_HOSTED": "1"}):  # importing must not pick up a local key or Ollama
    from operaitor.server import app as A
from operaitor.server.sessions import SessionManager, Settings


def _settings(max_sessions: int = 2) -> Settings:
    return Settings(hosted=True, llm_mode="auto", preset="", model="gpt-5-nano", chat_model="", diagnoser="",
                    max_sessions=max_sessions, idle_min=15, max_age_min=180, models=("gpt-5-nano",))


@pytest.fixture
def client():
    def make(max_sessions: int = 2) -> TestClient:
        A.manager = SessionManager(_settings(max_sessions))
        return TestClient(A.app)
    with mock.patch.dict(os.environ, {"PLANT_HOSTED": "1"}):
        yield make


def _until(ws, pred, limit: int = 400) -> dict:
    """Read messages until one satisfies pred (the server also streams snapshots, status, messages...)."""
    for _ in range(limit):
        msg = ws.receive_json()
        if pred(msg):
            return msg
    raise AssertionError("expected message not received")


def _ack(ws, request: str) -> dict:
    return _until(ws, lambda m: m.get("type") == "ack" and m.get("request") == request)


def test_health_config_and_console(client):
    with client() as c:
        h = c.get("/api/health").json()
        assert h["ok"] is True and h["max_sessions"] == 2 and h["sessions"] == 0
        cfg = c.get("/api/config").json()
        assert cfg["hosted"] is True and "groq" in cfg["providers"]
        assert all("api_key" not in p for p in cfg["providers"].values())  # no secrets in the public config
        assert c.get("/").status_code == 200


def test_websocket_hello_step_and_errors(client):
    with client() as c, c.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["api_version"] >= 8
        assert hello["session"]["hosted"] is True and hello["snapshot"]["time_min"] == 0.0
        ws.send_json({"type": "step", "minutes": 1})
        seen = []  # the step's snapshots arrive before its acknowledgement
        ack = _until(ws, lambda m: seen.append(m) or (m.get("type") == "ack" and m.get("request") == "step"))
        assert ack["ok"] is True
        assert max(m["time_min"] for m in seen if m.get("type") == "snapshot") == pytest.approx(1.0)
        ws.send_json({"type": "no_such_command"})
        assert _ack(ws, "no_such_command")["ok"] is False
        ws.send_json({"type": "set_agent_preset", "preset": "nonsense"})
        assert "unknown agent configuration" in _ack(ws, "set_agent_preset")["summary"]


def test_public_server_never_accepts_a_key(client):
    """A visitor's key cannot reach a public server: it refuses keys, and invalid connections, outright."""
    with client() as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        cases = [
            ({"provider": "nope", "in_browser": True}, "unknown provider"),
            ({"provider": "groq", "model": "m" * 200, "in_browser": True}, "invalid model"),
            ({"provider": "groq", "model": "llama-3.3-70b-versatile", "key": "gsk-x", "in_browser": True}, "send no key"),
            ({"provider": "groq", "model": "llama-3.3-70b-versatile", "key": "gsk-x"}, "never receives API keys"),
            ({"provider": "openai", "model": "gpt-5-nano", "key": "sk-x"}, "never receives API keys"),
        ]
        for msg, why in cases:
            ws.send_json({"type": "set_llm", **msg})
            ack = _ack(ws, "set_llm")
            assert ack["ok"] is False and why in ack["summary"], (msg, ack)


def _request_with_reset(ws, msg: dict) -> tuple[dict, dict]:
    """Send a command that rebuilds the agents; the server broadcasts the new state before acknowledging."""
    ws.send_json(msg)
    seen = []
    ack = _until(ws, lambda m: seen.append(m) or (m.get("type") == "ack" and m.get("request") == msg["type"]))
    return ack, next(m for m in seen if m.get("type") == "reset")


def _connect_in_browser(ws, provider: str = "groq", model: str = "llama-3.3-70b-versatile") -> dict:
    ack, reset = _request_with_reset(ws, {"type": "set_llm", "provider": provider, "model": model, "in_browser": True})
    assert ack["ok"] is True and "your browser" in ack["summary"], ack
    return reset


def test_custom_pipeline_runs_in_the_session_and_reference_comes_back(client):
    """A visitor's pipeline (settings, prompts, knowledge graph) runs in their session only; the reference returns."""
    from operaitor.agents.kg import KG_PATH
    with client() as c, c.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["session"]["pipeline"]["reference"] is True
        ws.send_json({"type": "pipeline_defaults"})
        d = _ack(ws, "pipeline_defaults")
        assert set(d["references"]) == set(A.VISITOR_PRESETS) and set(d["prompts"]) >= {"action", "planning"}
        assert "validator" in d["settings"]
        ws.send_json({"type": "check_kg", "kg": "{broken"})
        assert "not valid JSON" in _ack(ws, "check_kg")["summary"]
        ws.send_json({"type": "set_pipeline", "pipeline": {"base": "llm", "settings": {"reprompt_max": 99}}})
        bad = _ack(ws, "set_pipeline")
        assert bad["ok"] is False and "reprompt_max" in bad["summary"]

        _connect_in_browser(ws)
        spec = {"name": "my judge", "base": "llm", "settings": {"validator": "judge", "reprompt_max": 1},
                "prompts": {"action": {"mode": "append", "text": "Prefer setpoint changes."}},
                "kg": KG_PATH.read_text(encoding="utf-8")}
        ack, reset = _request_with_reset(ws, {"type": "set_pipeline", "pipeline": spec})
        assert ack["ok"] is True and "my judge" in ack["summary"] and ack["report"]["warnings"] == []
        p = reset["hello"]["session"]["pipeline"]
        assert p["reference"] is False and p["name"] == "my judge" and p["custom_kg"] is True
        assert p["prompts"] == ["action"] and "kg" not in p and "Prefer" not in str(p)  # no prompt / KG text sent back
        agent = reset["hello"]["agent"]
        assert agent["validator"] == "judge" and agent["reprompt_max"] == 1 and agent["custom_prompts"] == ["action"]

        ack, reset = _request_with_reset(ws, {"type": "set_agent_preset", "preset": "llm"})
        assert reset["hello"]["session"]["pipeline"]["reference"] is True
        assert reset["hello"]["agent"]["validator"] == "twin" and reset["hello"]["agent"]["custom_prompts"] == []


def test_chat_runs_through_the_browser_with_tools(client):
    """The chat assistant's model calls (including a tool call) go to the browser and back; no key travels."""
    with client() as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        reset = _connect_in_browser(ws)
        conn = reset["hello"]["session"]["llm"]["connection"]
        assert conn["in_browser"] is True and conn["provider"] == "groq"
        assert reset["hello"]["chat"]["mode"] == "llm"

        ws.send_json({"type": "chat", "text": "how is the plant?", "id": 7, "session": "console"})
        req = _until(ws, lambda m: m.get("type") == "llm_request")
        assert req["provider"] == "groq" and req["model"] == "llama-3.3-70b-versatile"
        # no key and no address to call: the browser decides where its key goes
        assert not {"key", "api_key", "base_url", "url", "authorization"} & set(req)
        assert "Bearer" not in str(req) and "sk-" not in str(req) and "https://api." not in str(req)
        assert {t["function"]["name"] for t in req["tools"]} >= {"plant_status", "trend"}
        ws.send_json({"type": "llm_response", "id": req["id"], "ok": True,
                      "message": {"content": "", "tool_calls": [{"id": "c1", "function": {"name": "plant_status", "arguments": "{}"}}]},
                      "usage": {"input_tokens": 900, "output_tokens": 12}})
        req2 = _until(ws, lambda m: m.get("type") == "llm_request")
        assert req2["messages"][-1]["role"] == "tool" and "time_min" in req2["messages"][-1]["content"]
        ws.send_json({"type": "llm_response", "id": req2["id"], "ok": True,
                      "message": {"content": "Running normally at 0.0 min."}, "usage": {"input_tokens": 1000, "output_tokens": 9}})
        reply = _until(ws, lambda m: m.get("type") == "chat")
        assert reply["id"] == 7 and reply["reply"] == "Running normally at 0.0 min." and reply["tools"] == ["plant_status"]
        usage = _until(ws, lambda m: m.get("type") == "llm_usage")
        assert usage["total"]["calls"] == 2 and usage["total"]["input_tokens"] == 1900


def test_browser_errors_reach_the_chat_and_disconnecting_clears(client):
    with client() as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        _connect_in_browser(ws, "openai", "gpt-5-nano")
        ws.send_json({"type": "chat", "text": "status?", "id": 1})
        req = _until(ws, lambda m: m.get("type") == "llm_request")
        assert req["temperature"] == 1.0 and req["reasoning_effort"] == "low"  # GPT-5 is a reasoning model
        ws.send_json({"type": "llm_response", "id": req["id"], "ok": False, "error": "OpenAI answered 429: rate limited"})
        reply = _until(ws, lambda m: m.get("type") == "chat")
        assert "429" in reply["reply"]
        ack, reset = _request_with_reset(ws, {"type": "clear_api_key"})
        assert ack["ok"] is True and reset["hello"]["session"]["llm"]["connection"] is None


def test_server_full_and_reattach(client):
    with client(max_sessions=1) as c:
        with c.websocket_connect("/ws") as ws1:
            sid = ws1.receive_json()["session"]["id"]
            with c.websocket_connect("/ws") as ws2:          # second visitor: no session free
                assert ws2.receive_json()["type"] == "busy"
            with c.websocket_connect(f"/ws?session={sid}") as ws3:  # same visitor, e.g. a reload
                assert ws3.receive_json()["session"]["id"] == sid
        assert c.get("/api/health").json()["sessions"] == 1


def test_model_switching_rules(client):
    """Visitors pick any model of their provider (they pay, from their browser); only a private server's own
    Ollama is switched on the server."""
    with client() as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        reset = _connect_in_browser(ws, "openai", "gpt-4.1-mini")      # not in PLANT_ALLOWED_MODELS: fine in-browser
        assert reset["hello"]["session"]["llm"]["connection"]["model"] == "gpt-4.1-mini"
        ws.send_json({"type": "set_model", "model": "qwen3:8b"})        # public server: no server-side Ollama
        ack = _ack(ws, "set_model")
        assert ack["ok"] is False and "only the Ollama on this machine" in ack["summary"]
        ws.send_json({"type": "list_models"})
        assert _ack(ws, "list_models")["models"] == []
