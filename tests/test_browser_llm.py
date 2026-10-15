"""LLM calls made by the visitor's browser (operaitor/agents/browser_llm.py): relay and chat model."""
from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from operaitor.agents.browser_llm import BrowserChatModel, BrowserRelay, to_ai_message
from operaitor.agents.llm import make_llm, use_session_llm
from operaitor.agents.providers import Endpoint


class FakeBrowser:
    """Stands in for a tab: records requests and answers them with a scripted reply."""

    def __init__(self, relay: BrowserRelay, reply=None):
        self.relay, self.requests, self.reply = relay, [], reply

    def __call__(self, msg: dict) -> None:
        self.requests.append(msg)
        if self.reply is not None:
            asyncio.get_running_loop().call_soon(self.relay.resolve, {"id": msg["id"], **self.reply(msg)})


def test_relay_needs_a_browser_and_resolves_in_order():
    async def run():
        relay = BrowserRelay(timeout_s=2)
        with pytest.raises(RuntimeError, match="no browser connected"):
            await relay.request({"model": "m"})
        tab = FakeBrowser(relay, lambda m: {"ok": True, "message": {"content": f"re {m['id']}"}})
        relay.attach(tab)
        a, b = await asyncio.gather(relay.request({}), relay.request({}))
        assert {a["message"]["content"], b["message"]["content"]} == {"re 1", "re 2"}
        assert relay.resolve({"id": 99, "ok": True}) is False          # nothing pending with that id
    asyncio.run(run())


def test_relay_errors_timeouts_and_disconnect():
    async def run():
        relay = BrowserRelay(timeout_s=0.2)
        relay.attach(FakeBrowser(relay, lambda m: {"ok": False, "error": "Groq answered 401: invalid key"}))
        with pytest.raises(RuntimeError, match="401"):
            await relay.request({})
        silent = FakeBrowser(relay)            # a newer tab that never answers
        relay.attach(silent)
        with pytest.raises(RuntimeError, match="did not answer"):
            await relay.request({})
        relay.timeout_s = 5
        pending = asyncio.ensure_future(relay.request({}))
        await asyncio.sleep(0.05)
        relay.detach(silent)                   # the older tab is still there: nothing fails yet
        assert not pending.done()
        relay.detach(relay._senders[0])         # last tab gone: what is waiting fails instead of hanging
        with pytest.raises(RuntimeError, match="disconnected"):
            await pending
    asyncio.run(run())


def test_make_llm_routes_a_browser_session_to_the_browser():
    async def run():
        relay = BrowserRelay(timeout_s=2)
        tab = FakeBrowser(relay, lambda m: {"ok": True, "message": {"content": '{"ok": true}'},
                                             "usage": {"input_tokens": 5, "output_tokens": 3}})
        relay.attach(tab)
        use_session_llm(Endpoint("gemini", "https://x", "", "gemini-2.5-flash", False, relay=relay), None)
        llm = make_llm("ignored", json_mode=True)
        assert isinstance(llm, BrowserChatModel) and llm.model == "gemini-2.5-flash"
        assert llm.json_mode is False                                   # Gemini's endpoint has no JSON mode
        msg = await llm.ainvoke([SystemMessage("sys"), HumanMessage("hi")])
        assert msg.content == '{"ok": true}' and msg.usage_metadata["total_tokens"] == 8
        req = tab.requests[0]
        assert req["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        assert "json" not in req and "tools" not in req and "key" not in req
    asyncio.run(run())


def test_tool_calls_and_tool_results_round_trip():
    msg = to_ai_message({"message": {"content": None, "tool_calls": [
        {"id": "a", "function": {"name": "trend", "arguments": '{"variable": "reactor pressure", "minutes": 10}'}},
        {"function": {"name": "plant_status", "arguments": {}}},                    # Ollama: object, no id
        {"id": "b", "function": {"name": "bad", "arguments": "{not json"}}]}})
    assert [c["name"] for c in msg.tool_calls] == ["trend", "plant_status", "bad"]
    assert msg.tool_calls[0]["args"] == {"variable": "reactor pressure", "minutes": 10}
    assert msg.tool_calls[1]["id"] and msg.tool_calls[2]["args"] == {"_raw": "{not json"}
    assert msg.content == "" and msg.usage_metadata["total_tokens"] == 0

    async def run():
        relay = BrowserRelay(timeout_s=2)
        tab = FakeBrowser(relay, lambda m: {"ok": True, "message": {"content": "done"}})
        relay.attach(tab)
        llm = BrowserChatModel(relay=relay, provider="groq", model="m").bind_tools([])
        await llm.ainvoke([HumanMessage("q"), msg, ToolMessage("{}", tool_call_id="a")])
        sent = tab.requests[0]["messages"]
        assert sent[1]["tool_calls"][0]["function"]["name"] == "trend" and sent[2]["role"] == "tool"
    asyncio.run(run())
