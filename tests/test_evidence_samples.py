import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.getcwd())
from gpt_researcher.evaluation.evidence_samples import extract_body_candidates, evaluate_local_references
from gpt_researcher.evaluation.entity_evaluator import evaluate_report_entities
from gpt_researcher.evaluation.source_evaluator import evaluate_public_url_sources

REPORT = '# 研究\n\n## 1 分析\n普惠GTF发动机维修量为120台[原文1]。\n\n## 证据来源列表\n- [原文1] engine.pdf\n'
CATALOG = {'sources': [{'locator': 'engine.pdf', 'pages': [{'page': 3, 'text': '普惠GTF发动机维修量为120台。'}]}]}


class EvidenceSampleTests(unittest.TestCase):
    def test_unreadable_public_source_has_no_measured_accuracy(self):
        report = '## 分析\nGTF维修120台[URL1]。\n## 证据来源列表\n- [URL1] https://example.com/gtf'
        with patch('gpt_researcher.evaluation.source_evaluator._read_url_text', return_value=''):
            result = evaluate_public_url_sources(report)
        self.assertEqual(result['cited_url_ref_count'], 1)
        self.assertEqual(result['unchecked_count'], 1)
        self.assertIsNone(result['support_accuracy'])
        self.assertIsNone(result['requirement_met'])

    def test_body_fallback_uses_saved_original_without_network(self):
        with patch('gpt_researcher.evaluation.entity_evaluator._load_ground_truth', return_value=(None, [])), \
             patch('gpt_researcher.evaluation.entity_evaluator._read_url_text', side_effect=AssertionError('network')), \
             patch('gpt_researcher.evaluation.entity_evaluator._read_local_source_text', side_effect=AssertionError('reread')):
            result = evaluate_report_entities(REPORT, 'test', source_catalog=CATALOG)
        self.assertEqual(result['method'], 'body_rule_candidates')
        self.assertGreater(result['extracted_count'], 0)
        self.assertIsNone(result['accuracy'])
        self.assertEqual(result['auto_evidence_eval']['strict_auto_evidence_accuracy'], 1)
        self.assertEqual(result['extracted_entities'][0]['auto_evidence_check']['checked_refs'][0]['page'], 3)

    def test_missing_source_and_uncited_candidates_are_not_dropped(self):
        report = REPORT.replace('[原文1]。', '。')
        with patch('gpt_researcher.evaluation.entity_evaluator._load_ground_truth', return_value=(None, [])):
            result = evaluate_report_entities(report, 'test', source_catalog=CATALOG)
        self.assertGreater(result['extracted_count'], 0)
        self.assertEqual(result['auto_evidence_eval']['checked_count'], result['extracted_count'])
        self.assertTrue(all(e['auto_evidence_check']['evidence_basis'] == 'saved_source_retrieval'
                            for e in result['extracted_entities']))

    def test_wrong_value_is_not_full_support_even_if_model_name_matches(self):
        with patch('gpt_researcher.evaluation.entity_evaluator._load_ground_truth', return_value=(None, [])):
            result = evaluate_report_entities(REPORT.replace('120台', '999台'), 'test', source_catalog=CATALOG)
        self.assertEqual(result['auto_evidence_eval']['supported_count'], 0)

    def test_references_are_not_borrowed_from_next_sentence(self):
        samples = extract_body_candidates('## 1 分析\nGTF产能120台。LEAP-1A产能200台[原文1]。')
        self.assertEqual(next(s for s in samples if s['name'] == '120台')['evidence'], '')
        self.assertEqual(next(s for s in samples if s['name'] == '200台')['evidence'], '[原文1]')

    def test_internal_verification_and_source_lists_do_not_become_samples(self):
        samples = extract_body_candidates('## 待核验事项（内部核验）\nGTF120台[原文1]\n## 参考文献\nGTF论文2025年')
        self.assertEqual(samples, [])

    def test_local_sources_are_measured_separately_from_public_urls(self):
        local = evaluate_local_references(REPORT, CATALOG)
        self.assertEqual(local['readable_rate'], 1)
        public = evaluate_public_url_sources(REPORT)
        self.assertEqual(public['cited_url_ref_count'], 0)
        self.assertIsNone(public['requirement_met'])

    def test_ambiguous_or_unreadable_originals_are_reported(self):
        self.assertEqual(evaluate_local_references(REPORT, {'sources': []})['readable_count'], 0)
        self.assertEqual(evaluate_local_references(REPORT, {'sources': CATALOG['sources'] * 2})['readable_count'], 0)
