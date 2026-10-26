from __future__ import annotations
import unittest
from types import SimpleNamespace
from maestro_gateway.mission import _benchmark_contract_retry_stays_on_resource
from maestro_gateway.lossless_sentence_repair import propose_lossless_two_sentence_line_repair
from maestro_gateway.response_constraints import evaluate_shape

class Fix10BenchmarkFormatRetryStabilityTests(unittest.TestCase):
    def selection(self, reason='user_language_benchmark_evidence'):
        return SimpleNamespace(reason=reason)

    def test_benchmark_arabic_two_sentence_shape_retry_stays_on_resource(self):
        p='أجب بالعربية الفصحى الطبيعية في جملتين فقط. ما هو MAESTRO وما وظيفته؟'
        self.assertTrue(_benchmark_contract_retry_stays_on_resource(
            self.selection(),p,'explicit_shape_expected_two_sentences_got_1'))

    def test_benchmark_arabic_script_retry_stays_on_resource(self):
        p='أجب بالعربية فقط ولا تستخدم كلمات إنجليزية.'
        self.assertTrue(_benchmark_contract_retry_stays_on_resource(
            self.selection(),p,'explicit_shape_arabic_only_foreign_script'))

    def test_benchmark_instruction_echo_retry_stays_on_resource(self):
        p='أجب بالعربية فقط: ما هو MAESTRO؟'
        self.assertTrue(_benchmark_contract_retry_stays_on_resource(
            self.selection(),p,'user_instruction_echo'))

    def test_semantic_failure_remains_failover_eligible(self):
        p='أجب بالعربية فقط: ما هو MAESTRO؟'
        self.assertFalse(_benchmark_contract_retry_stays_on_resource(
            self.selection(),p,'deterministic_semantic_mismatch:test'))

    def test_nonbenchmark_primary_keeps_legacy_failover(self):
        p='أجب بالعربية فقط: ما هو MAESTRO؟'
        self.assertFalse(_benchmark_contract_retry_stays_on_resource(
            self.selection('primary_reused'),p,'explicit_shape_expected_two_sentences_got_1'))

    def test_lossless_two_arabic_lines_receive_only_terminal_punctuation(self):
        p='أجب بالعربية الفصحى الطبيعية في جملتين فقط. ما هو MAESTRO وما وظيفته؟'
        a='MAESTRO هو مساعد ذكاء اصطناعي محلي على هذا الحاسوب\nوظيفته مساعدتك في الإجابة عن الأسئلة وتنظيم المهام'
        repaired=propose_lossless_two_sentence_line_repair(p,a)
        self.assertEqual(repaired, a.splitlines()[0]+'.\n'+a.splitlines()[1]+'.')
        self.assertEqual(evaluate_shape(p,repaired).status,'PASS_SHAPE')

    def test_lossless_repair_abstains_on_single_line(self):
        p='أجب بالعربية الفصحى الطبيعية في جملتين فقط. ما هو MAESTRO؟'
        self.assertIsNone(propose_lossless_two_sentence_line_repair(p,'MAESTRO هو مساعد محلي على هذا الحاسوب'))

    def test_lossless_repair_abstains_on_numbered_list(self):
        p='أجب بالعربية الفصحى الطبيعية في جملتين فقط. ما هو MAESTRO؟'
        self.assertIsNone(propose_lossless_two_sentence_line_repair(p,'1. MAESTRO مساعد محلي\n2. يساعد في المهام'))

if __name__=='__main__': unittest.main()
