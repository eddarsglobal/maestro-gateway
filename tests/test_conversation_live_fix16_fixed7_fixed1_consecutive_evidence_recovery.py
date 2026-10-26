"""Two recent rejected missions outweigh older technical acceptances."""
from __future__ import annotations
import unittest
from pathlib import Path
from maestro_gateway.language_quality_governor import SessionLanguageEvidence

R='local::authorized'; V='e'*64
class Fix16Fixed7Fixed1ConsecutiveEvidenceRecovery(unittest.TestCase):
    def test_two_rejections_after_three_technical_acceptances_trigger_trial(self):
        s=SessionLanguageEvidence()
        for _ in range(3):
            self.assertTrue(s.observe(R,'ar',V,accepted=True,reason='accepted'))
        for _ in range(2):
            self.assertTrue(s.observe(R,'ar',V,accepted=False,reason='identity_slot_not_one_sentence'))
        e=s.evidence(R,'ar',V)
        self.assertEqual((e.completed,e.rejected,e.consecutive_rejected),(3,2,2))
        self.assertTrue(s.should_trial_alternative(R,'ar',V))
    def test_last_live_arabic_role_failure_is_attributed(self):
        s=SessionLanguageEvidence()
        self.assertTrue(s.observe(R,'ar',V,accepted=False,reason='maestro_role_not_grounded'))
        self.assertEqual(s.evidence(R,'ar',V).rejected,1)
    def test_last_live_arabic_pair_enables_trial(self):
        s=SessionLanguageEvidence()
        s.observe(R,'ar',V,accepted=False,reason='maestro_role_not_grounded')
        s.observe(R,'ar',V,accepted=False,reason='identity_slot_not_one_sentence')
        self.assertTrue(s.should_trial_alternative(R,'ar',V))
    def test_one_failure_does_not_trigger(self):
        s=SessionLanguageEvidence()
        s.observe(R,'de',V,accepted=False,reason='maestro_role_not_grounded')
        self.assertEqual(s.evidence(R,'de',V).consecutive_rejected,1)
        self.assertFalse(s.should_trial_alternative(R,'de',V))
    def test_accepted_interrupts_consecutive_streak_without_fluency_claim(self):
        s=SessionLanguageEvidence()
        for _ in range(2): s.observe(R,'ar',V,accepted=False,reason='maestro_identity_not_grounded')
        self.assertTrue(s.should_trial_alternative(R,'ar',V))
        s.observe(R,'ar',V,accepted=True,reason='accepted')
        self.assertFalse(s.should_trial_alternative(R,'ar',V))
        self.assertEqual(s.evidence(R,'ar',V).consecutive_rejected,0)
        self.assertFalse(s.snapshot()['fluency_certified'])
    def test_technical_success_does_not_erase_total_negative_evidence(self):
        s=SessionLanguageEvidence()
        s.observe(R,'ar',V,accepted=False,reason='linguistic_false_translated_identity')
        s.observe(R,'ar',V,accepted=True,reason='accepted')
        e=s.evidence(R,'ar',V)
        self.assertEqual((e.rejected,e.completed),(1,1))
        self.assertEqual(e.consecutive_rejected,0)
    def test_unrelated_failures_do_not_count(self):
        s=SessionLanguageEvidence()
        for reason in ('timeout','cancellation','transport_failure'):
            self.assertFalse(s.observe(R,'ar',V,accepted=False,reason=reason))
        self.assertFalse(s.should_trial_alternative(R,'ar',V))
    def test_two_failures_in_one_mission_not_multiple_observations(self):
        txt=(Path(__file__).parents[1]/'src/maestro_gateway/mission.py').read_text()
        self.assertEqual(txt.count('primary_intelligence.note_language_quality('),1)
    def test_language_and_model_digest_isolation(self):
        s=SessionLanguageEvidence()
        for _ in range(2): s.observe(R,'ar',V,accepted=False,reason='identity_slot_not_one_sentence')
        for lang,model,digest in [('de',R,V),('ar','local::other',V),('ar',R,'f'*64)]:
            self.assertFalse(s.should_trial_alternative(model,lang,digest))
    def test_no_stored_text_in_read_only_snapshot(self):
        s=SessionLanguageEvidence()
        for _ in range(2): s.observe(R,'ar',V,accepted=False,reason='identity_slot_not_one_sentence')
        data=s.scoped_snapshot(R,V)
        self.assertTrue(data['version_bound'])
        self.assertTrue(data['languages']['ar']['trial_due'])
        self.assertEqual(data['languages']['ar']['rejected'],2)
        self.assertFalse(data['fluency_certified'])
        self.assertNotIn('identity_slot_not_one_sentence',str(data))
        self.assertNotIn(R,str(data))
    def test_stale_digest_never_returns_counts(self):
        s=SessionLanguageEvidence()
        s.observe(R,'ar',V,accepted=False,reason='maestro_identity_not_grounded')
        self.assertFalse(s.scoped_snapshot(R,'unknown')['version_bound'])
        self.assertFalse(s.scoped_snapshot(R,'f'*64)['languages'])
    def test_primary_snapshot_exposes_version_bound_counters(self):
        txt=(Path(__file__).parents[1]/'src/maestro_gateway/primary_intelligence.py').read_text()
        self.assertIn('current_primary_language_evidence',txt)
        self.assertIn('scoped_snapshot(',txt)
    def test_no_vendor_steering_or_background_compute(self):
        src=(Path(__file__).parents[1]/'src/maestro_gateway/language_quality_governor.py').read_text()
        for forbidden in ['ollama::llama3.2','qwen2.5-coder:3b','download_model','cloud_endpoint']:
            self.assertNotIn(forbidden,src)
    def test_snapshot_for_old_version_not_certified(self):
        s=SessionLanguageEvidence()
        snap=s.scoped_snapshot(R,V)
        self.assertTrue(snap['version_bound'])
        self.assertEqual(snap['languages'],{})
        self.assertFalse(snap['fluency_certified'])
if __name__=='__main__':unittest.main()
