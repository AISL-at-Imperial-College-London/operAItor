"""Human-operator mode: agents stand down, a benchmark scenario is replayed for a person, blind."""
import asyncio
import tempfile
import unittest
from pathlib import Path

from operaitor.server.human_test import schedule
from operaitor.server.plant_service import PlantService


async def _started(scenario: str = "S01", blind: bool = True) -> PlantService:
    s = PlantService()
    s.campaign_dir = Path(tempfile.mkdtemp())
    s.pace_every = 0
    events: list[dict] = []
    q = s.subscribe(maxsize=100_000)
    s._events = (q, events)
    ack = await s.handle_command({"type": "human_test_start", "scenario": scenario, "blind": blind, "run": False})
    assert ack["ok"], ack
    return s


def _drain(s: PlantService) -> list[dict]:
    q, events = s._events
    while not q.empty():
        events.append(q.get_nowait())
    return events


class HumanTestMode(unittest.TestCase):
    def test_blind_test_hides_the_fault_and_scores_like_the_benchmark(self):
        async def scenario():
            s = await _started("S01", blind=True)
            self.assertEqual(s.operator_mode, "human")
            await s.step(12.0)  # past the onset at 10 min
            snap = s.snapshot()
            self.assertTrue(snap["truth"].get("hidden"))
            self.assertEqual(snap["truth"]["pump"], [])
            self.assertEqual(len(s.env.spec.pump_faults.faults), 1)  # injected all the same
            text = " ".join(str(e) for e in _drain(s) if e["type"] != "reset")  # reset carries the static meta
            self.assertNotIn("reflux_pump", text)
            self.assertNotIn("P-2", " ".join(m["text"] for m in s.messages if "inject" in m["text"]))
            st = s.human_test_status()["human_test"]
            self.assertEqual(st["faults_injected"], 1)
            self.assertNotIn("faults", st)
            # manual injections and clearing are refused while blind
            self.assertFalse((await s.handle_command({"type": "clear_injections"}))["ok"])
            # the operator's actions are logged
            await s.handle_command({"type": "ack_all"})
            self.assertEqual(len(s.human_test.actions), 1)
            # handing back to the agents mid-test is refused; stopping reveals the fault and writes a report
            self.assertFalse((await s.handle_command({"type": "operator_mode", "mode": "agents"}))["ok"])
            await s.handle_command({"type": "human_test_stop"})
            st = s.human_test_status()["human_test"]
            self.assertEqual(st["state"], "stopped early")
            self.assertEqual(st["faults"][0]["fault"], "reflux_pump_trip")
            self.assertTrue(Path(st["report_path"]).exists())
            self.assertFalse(s.snapshot()["truth"].get("hidden"))
            self.assertTrue((await s.handle_command({"type": "operator_mode", "mode": "agents"}))["ok"])
        asyncio.run(scenario())

    def test_unattended_test_runs_to_its_window_like_no_agents(self):
        async def scenario():
            s = await _started("S01", blind=False)
            _, window = schedule("S01")
            await s.step(window + 5)
            st = s.human_test_status()["human_test"]
            self.assertEqual(st["state"], "complete")
            self.assertIsNotNone(st["stop_min"])  # nobody acted: the reflux pump trip stops the plant
            self.assertLess(st["profit"], 0)
            self.assertIn("faults", st)
        asyncio.run(scenario())

    def test_agents_stand_down_in_human_mode(self):
        async def scenario():
            s = PlantService()
            ack = await s.handle_command({"type": "operator_mode", "mode": "human"})
            self.assertTrue(ack["ok"])
            self.assertEqual(s.status()["operator_mode"], "human")
            self.assertFalse((await s.handle_command({"type": "operator_mode", "mode": "robot"}))["ok"])
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
