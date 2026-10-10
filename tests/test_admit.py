"""ADMIT integration: cartridge compiles, the live gate admits a correct record and rejects bad ones.

Skipped when admit-bench is not installed (pip install "operaitor[admit]").
"""
from __future__ import annotations

import asyncio
import unittest

from operaitor.admit import available

ADMIT, WHY = available()


@unittest.skipUnless(ADMIT, WHY)
class AdmitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from operaitor.admit.build import _decision_point
        from operaitor.admit.gate import AdmitGate
        from operaitor.agents.plan_compiler import compile_plan
        cls.gate = AdmitGate()
        # S01 (reflux pump trip) at the moment PCA confirms the fault
        cls.env, cls.obs, cls.det, cls.diag = asyncio.run(_decision_point(0))
        cls.cmd = {"type": "START_SPARE", "target": "REFLUX"}
        cls.action, errors = compile_plan({"commands": [cls.cmd], "reasoning": ""}, cls.obs, cls.env.action_catalog())
        assert not errors, errors

    def evaluate(self, record):
        from operaitor.agents.recovery_graph import clone_env
        return self.gate.evaluate(clone_env(self.env), self.action, [self.cmd], [record],
                                  self.obs, self.det, self.diag, None)

    def test_cartridge_compiles(self):
        c = self.gate.cartridge
        self.assertEqual(c.manifest["world"], "acetone_plant")
        self.assertGreaterEqual(len(c.cases), 15)
        self.assertFalse(c.warnings)

    def test_good_record_is_admissible(self):
        v = self.evaluate({"cited_evidence": ["ev_pumps", "ev_pca"], "checks_performed": ["verify_pump_failed"],
                           "confidence": 0.9, "reversibility": "undoable", "hazard_id": "H-COLUMN-DRY",
                           "diagnosis": "reflux pump trip, trays draining", "rationale": "standby pump restores reflux"})
        self.assertTrue(v["admissible"], v["records"][0]["violations"])
        self.assertEqual(v["mode"], "upset")
        traj = v["records"][0]["trajectories"]
        self.assertFalse(traj["action_trajectory"]["crossed"])      # standby pump keeps the column wet
        self.assertTrue(traj["null_trajectory"]["crossed"])         # doing nothing trips COL_L_LL

    def test_missing_record_is_rejected(self):
        v = self.evaluate(None)
        self.assertFalse(v["admissible"])
        codes = {x["code"] for x in v["records"][0]["violations"]}
        self.assertTrue(codes & {"AAS-T0-BAD-FIELD", "AAS-T1-EVIDENCE-MISSING", "AAS-T3-STEP-MISSING"}, codes)
        self.assertTrue(v["remedies"])

    def test_untrusted_diagnosis_is_not_evidence(self):
        v = self.evaluate({"cited_evidence": ["ev_diagnosis"], "checks_performed": ["verify_pump_failed"],
                           "confidence": 0.9, "reversibility": "undoable", "hazard_id": "H-COLUMN-DRY",
                           "diagnosis": "reflux pump trip"})
        self.assertFalse(v["admissible"])
        self.assertIn("AAS-T1-EVIDENCE-MISSING", {x["code"] for x in v["records"][0]["violations"]})


if __name__ == "__main__":
    unittest.main()
