from __future__ import annotations
import unittest
from pathlib import Path

class Fix16Fixed1LegacyTestSupersessionAlignmentTests(unittest.TestCase):
    def _mission(self):
        return (Path(__file__).parents[1]/"src/maestro_gateway/mission.py").read_text(encoding="utf-8")

    def test_fix15_legacy_monolithic_transcript_is_not_reintroduced(self):
        src=self._mission()
        self.assertNotIn("transcript = identity_fact_instruction or base_instruction",src)
        self.assertIn("structured_identity_slot_total = structured_identity_slot_count",src)

    def test_identity_remains_fact_grounded_and_model_generated(self):
        src=self._mission()
        self.assertIn("identity_fact_instruction = fact_grounded_identity_instruction",src)
        self.assertIn("adapter = registry.adapter(resource_id)",src)
        self.assertIn("structured_identity_slot_total",src)
        self.assertNotIn("identity_core_candidate",src)

    def test_no_canonical_user_facing_identity_answer_returns(self):
        root=Path(__file__).parents[1]/"src/maestro_gateway"
        joined="\n".join((root/n).read_text(encoding="utf-8") for n in ("mission.py","native_linguistic_revision.py","response_effort.py"))
        self.assertNotIn("MAESTRO ist dein lokaler KI-Assistent auf diesem Computer. Er unterstützt dich",joined)
        self.assertNotIn("MAESTRO هو مساعد ذكاء اصطناعي محلي يعمل على هذا الحاسوب. يساعدك",joined)

    def test_structured_identity_keeps_bounded_same_resource_runtime_contract(self):
        src=self._mission()
        self.assertIn("adapter = registry.adapter(resource_id)",src)
        self.assertIn("structured_identity_slot_retry_counts",src)
        self.assertIn("structured_identity_slot_accepted",src)
        self.assertIn('"resource_id": resource_id',src)

if __name__ == "__main__":
    unittest.main()
