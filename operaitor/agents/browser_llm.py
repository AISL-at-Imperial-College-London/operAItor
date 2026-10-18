"""LLM calls made by the visitor's browser: the API key never reaches this server.

When a visitor connects a provider in the public console, their browser keeps the key in memory and
calls the provider itself. The agents still run here: each model call becomes an ``llm_request``
message to that visitor's browser, which calls the provider (Groq, Gemini, OpenRouter, OpenAI or the
visitor's own Ollama) and answers with an ``llm_response``. The server never sees the key, and the
browser only sends it to the provider address the visitor chose, never to one the server names.

``BrowserRelay`` is one per session (pending requests and the connected browser), and
``BrowserChatModel`` is a LangChain chat model that goes through it, so every agent (including the
chat assistant's tool calls) works unchanged.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field


class BrowserRelay:
    """A session's link to its visitor's browser for LLM calls."""

    def __init__(self, timeout_s: float | None = None):
        self.timeout_s = timeout_s or float(os.environ.get("PLANT_BROWSER_LLM_TIMEOUT", "240"))
        self._senders: list[Callable[[dict], None]] = []   # connected tabs; the latest one is asked
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)

    def attach(self, send: Callable[[dict], None]) -> None:
        self._senders.append(send)

    def detach(self, send: Callable[[dict], None]) -> None:
        if send in self._senders:
            self._senders.remove(send)
        if not self._senders:  # nobody left to answer: fail what is waiting instead of hanging
            self.fail_all("the browser that holds the key disconnected")

    def fail_all(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self._pending.clear()

    async def request(self, payload: dict) -> dict:
        if not self._senders:
            raise RuntimeError("no browser connected to make the LLM call (the key lives only in the visitor's tab)")
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            self._senders[-1]({"type": "llm_request", "id": rid, **payload})
            return await asyncio.wait_for(fut, self.timeout_s)
        except asyncio.TimeoutError:
            raise RuntimeError(f"the browser did not answer the LLM call within {self.timeout_s:.0f} s") from None
        finally:
            self._pending.pop(rid, None)

    def resolve(self, msg: dict) -> bool:
        """An ``llm_response`` from the browser; False if it answers nothing pending (late or unknown)."""
        fut = self._pending.get(msg.get("id"))
        if fut is None or fut.done():
            return False
        if msg.get("ok"):
            fut.set_result(msg)
        else:
            fut.set_exception(RuntimeError(str(msg.get("error") or "the LLM call failed in the browser")[:500]))
        return True


class BrowserChatModel(BaseChatModel):
    """A chat model whose calls are made by the visitor's browser (see module docstring)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    relay: Any = Field(exclude=True)
    provider: str
    model: str
    temperature: float | None = 0.0
    json_mode: bool = False
    reasoning_effort: str | None = None
    tools: list[dict] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "browser-relay"

    def bind_tools(self, tools: list, **kwargs: Any) -> "BrowserChatModel":
        return self.model_copy(update={"tools": [convert_to_openai_tool(t) for t in tools]})

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        raise RuntimeError("browser LLM calls are asynchronous; use ainvoke")

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
                         **kwargs: Any) -> ChatResult:
        request: dict[str, Any] = {"provider": self.provider, "model": self.model,
                                   "messages": convert_to_openai_messages(messages)}
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort
        if self.json_mode:
            request["json"] = True
        if self.tools:
            request["tools"] = self.tools
        reply = await self.relay.request(request)
        return ChatResult(generations=[ChatGeneration(message=to_ai_message(reply))])


def to_ai_message(reply: dict) -> AIMessage:
    """The browser's answer (OpenAI chat-completion message + usage) as an AIMessage."""
    m = reply.get("message") or {}
    calls = []
    for c in m.get("tool_calls") or []:
        fn = c.get("function") or {}
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {"_raw": args}
        calls.append({"name": fn.get("name", ""), "args": args, "id": c.get("id") or f"call_{len(calls)}"})
    u = reply.get("usage") or {}
    tin, tout = int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0)
    usage = {"input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout}
    return AIMessage(content=m.get("content") or "", tool_calls=calls, usage_metadata=usage,
                     response_metadata={"via": "browser"})
