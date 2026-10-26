"""No cloud dependency and no accidental deterministic-Core promotion in gateway wiring."""
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
class WiringTests(unittest.TestCase):
    def test_manager_has_epoch_guard_and_never_unconditionally_promotes(self):
        source=(ROOT/'src/maestro_gateway/primary_intelligence.py').read_text()
        ast.parse(source)
        self.assertIn('self._primary != resource_id or self._epoch != expected_epoch',source)
        self.assertIn('note_sovereign_observation(',source)
        self.assertIn('verified_version(resource_id)',source)
        self.assertIn("'store_unavailable_'",source)
    def test_gateway_receipt_does_not_proclaim_fluency_or_objective(self):
        source=(ROOT/'src/maestro_gateway/mission.py').read_text()
        ast.parse(source)
        self.assertIn("['p04_objective_certified'] = False",source)
        self.assertIn("['p04_fluency_certified'] = False",source)
        self.assertIn('not deterministic_core_delivery and resource_id',source)
        self.assertIn("metrics.get('response_origin') == 'local_model'",source)
    def test_no_network_or_inference_in_ledger(self):
        source=(ROOT/'src/maestro_gateway/sovereign_evidence_ledger.py').read_text()
        names={n.module for n in ast.walk(ast.parse(source)) if isinstance(n,ast.ImportFrom)}
        self.assertNotIn('urllib',names)
        self.assertNotIn('requests',names)
        self.assertNotIn('ollama',names)
        self.assertNotIn('maestro',names)
    def test_versioned_store_is_negative_only(self):
        source=(ROOT/'src/maestro_gateway/sovereign_evidence_ledger.py').read_text()
        self.assertIn("raise ValueError('unsupported qualification status; no automatic OBJECTIVE_VERIFIED')",source)
        self.assertIn("'qualified': False",source)
        self.assertIn("'linguistic_certified': False",source)
if __name__=='__main__':unittest.main()
