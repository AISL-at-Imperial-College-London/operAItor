"""Hosted-app sessions: isolation, cap, reattach, visitor keys never leak (no network)."""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest import mock

from acetone_plant.server import sessions as S
from acetone_plant.server.sessions import SessionManager, Settings


def _settings(**kw) -> Settings:
    return Settings(hosted=True, llm_mode="auto", preset="", model="gpt-5-nano", chat_model="", diagnoser="",
                    max_sessions=kw.get("max_sessions", 2), idle_min=15, max_age_min=180,
                    models=("gpt-5-nano",))


class SessionTests(unittest.TestCase):
    def setUp(self):  # hosted mode: no server key is ever used, so no test can call OpenAI
        patcher = mock.patch.dict(os.environ, {"PLANT_HOSTED": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_isolation_cap_and_reattach(self):
        async def run():
            m = SessionManager(_settings(max_sessions=2))
            a = await m.attach(None)
            b = await m.attach(None)
            self.assertNotEqual(a.id, b.id)
            self.assertIsNone(await m.attach(None))            # full
            self.assertIs(await m.attach(a.id), a)               # reattach is allowed when full
            await a.service.handle_command({"type": "inject", "kind": "pump", "pump_id": "P-201A"})
            await a.service.step(0.5)
            await b.service.step(0.5)
            self.assertTrue(a.service.env.observation()["pump_trains"]["REFLUX"]["selected_failed"])
            self.assertFalse(b.service.env.observation()["pump_trains"]["REFLUX"]["selected_failed"])
            await m.stop()
        asyncio.run(run())

    def test_hosted_ignores_server_key_and_never_returns_visitor_key(self):
        async def run():
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-server-key-should-not-be-used", "PLANT_HOSTED": "1"}):
                m = SessionManager(_settings())
                s = await m.attach(None)
                hello = s.service.hello()
                self.assertIsNone(hello["session"]["llm"]["key_source"])     # server key ignored
                self.assertFalse(hello["session"]["llm"]["active"])
                key = "sk-test-visitor-key-0123456789abcdef"
                await s.configure_agents(api_key=key)
                hello = s.service.hello()
                self.assertEqual(hello["session"]["llm"]["key_source"], "visitor")
                self.assertNotIn(key, json.dumps(hello, default=str))
                await s.configure_agents(api_key="")                         # remove the key
                self.assertIsNone(s.service.hello()["session"]["llm"]["key_source"])
                await m.stop()
        asyncio.run(run())

    def test_key_check_rejects_without_network_errors_leaking(self):
        class Resp:
            status_code = 401

        class Client:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return Resp()

        with mock.patch("httpx.AsyncClient", Client):
            ok, reason = asyncio.run(S.check_openai_key("sk-bad"))
        self.assertFalse(ok)
        self.assertIn("401", reason)


if __name__ == "__main__":
    unittest.main()
