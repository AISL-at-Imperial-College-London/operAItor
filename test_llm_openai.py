"""OpenAI wiring without network calls: the real ChatOpenAI client over a mocked HTTP transport."""
import asyncio
import json
import os
import unittest

try:
    import httpx
    import langchain_openai  # noqa: F401
    HAVE_OPENAI = True
except ImportError:
    HAVE_OPENAI = False

from agents.llm import DEFAULT_MODEL, PRICES_PER_1M, USAGE, is_reasoning_model, llm_status, make_llm


def _completion(content="", tool_calls=None, prompt_tokens=1000, completion_tokens=200):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": DEFAULT_MODEL,
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens}}


class MockOpenAI:
    """Records request bodies and replays canned chat.completions responses."""

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def client(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append({"path": request.url.path, "body": json.loads(request.content)})
            return httpx.Response(200, json=self.responses.pop(0))
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class StatusTests(unittest.TestCase):
    def test_default_is_cheapest_priced_model(self):
        self.assertEqual(DEFAULT_MODEL, min(PRICES_PER_1M, key=lambda m: sum(PRICES_PER_1M[m])))
        self.assertTrue(is_reasoning_model(DEFAULT_MODEL))

    def test_status_explains_missing_key(self):
        import agents.llm as llm_mod
        old = os.environ.pop("OPENAI_API_KEY", None)
        old_env, old_loaded = os.environ.get("PLANT_DOTENV"), llm_mod._env_loaded
        os.environ["PLANT_DOTENV"] = "does-not-exist.env"   # ignore the repo's real .env
        llm_mod._env_loaded = False
        try:
            ok, reason = llm_status(DEFAULT_MODEL)
            self.assertFalse(ok)
            self.assertTrue("OPENAI_API_KEY" in reason or "langchain-openai" in reason)
        finally:
            llm_mod._env_loaded = old_loaded
            if old is not None:
                os.environ["OPENAI_API_KEY"] = old
            if old_env is None:
                os.environ.pop("PLANT_DOTENV", None)
            else:
                os.environ["PLANT_DOTENV"] = old_env


@unittest.skipUnless(HAVE_OPENAI, "langchain-openai not installed")
class OpenAIRequestTests(unittest.TestCase):
    def test_action_agent_request_and_cost(self):
        from agents.action import LLMActionAgent
        from recovery_agents_langgraph import build_episode

        mock = MockOpenAI([_completion(json.dumps({
            "commands": [{"type": "START_SPARE", "target": "REFLUX"}], "reasoning": "r",
            "kg_evidence": ["action_START_SPARE"]}))])
        llm = make_llm(DEFAULT_MODEL, json_mode=True, api_key="sk-test", http_async_client=mock.client())
        agent = LLMActionAgent(DEFAULT_MODEL, llm=llm)
        env = build_episode("reflux_pump_trip", 5, 1, 0)
        obs = env.observation()
        ctx = {"observation": obs, "catalog": env.action_catalog(), "brief": {"target_fault": "reflux_pump_trip"},
               "detection": {"pca": None, "rules": {}, "detectors": {}},
               "diagnosis": {"hypotheses": [], "contributors": [], "summary": "reflux_pump_trip"}, "knowledge": []}
        plan = asyncio.run(agent.propose(ctx))
        self.assertEqual(plan["commands"], [{"type": "START_SPARE", "target": "REFLUX"}])

        req = mock.requests[0]
        self.assertTrue(req["path"].endswith("/chat/completions"))
        body = req["body"]
        self.assertEqual(body["model"], "gpt-5-nano")
        self.assertIn(body.get("temperature", 1), (1, 1.0))    # reasoning models accept only temperature=1
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        price_in, price_out = PRICES_PER_1M["gpt-5-nano"]
        self.assertAlmostEqual(agent.last_usage["cost_usd"], (1000 * price_in + 200 * price_out) / 1e6)

    def test_chat_agent_tool_loop_over_openai(self):
        from agents.chat import ChatAgent
        from agents.kg import KnowledgeGraph
        from server.plant_service import PlantService

        mock = MockOpenAI([
            _completion(tool_calls=[{"id": "call_1", "type": "function", "function": {
                "name": "knowledge_graph", "arguments": json.dumps({"query": "R_PIC"})}}]),
            _completion("R_PIC is the reactor pressure controller (`controller_R_PIC`)."),
        ])

        async def scenario():
            s = PlantService()
            await s.load({"scenario": "nominal", "horizon_min": 10})
            llm = make_llm(DEFAULT_MODEL, api_key="sk-test", http_async_client=mock.client())
            chat = ChatAgent(s, KnowledgeGraph(), llm=llm)
            chat.model = DEFAULT_MODEL
            return await chat.ask("what is R_PIC?", session="t")

        before = USAGE.summary()["by_agent"].get("chat", {}).get("calls", 0)
        out = asyncio.run(scenario())
        self.assertEqual(out["tools"], ["knowledge_graph"])
        self.assertIn("controller_R_PIC", out["reply"])
        first = mock.requests[0]["body"]
        self.assertIn("knowledge_graph", [t["function"]["name"] for t in first["tools"]])
        second = mock.requests[1]["body"]["messages"]
        self.assertEqual(second[-1]["role"], "tool")          # tool result was sent back to the model
        self.assertIn("PO_R_PIC", second[-1]["content"] + json.dumps(second))
        self.assertEqual(USAGE.summary()["by_agent"]["chat"]["calls"], before + 2)


if __name__ == "__main__":
    unittest.main()
