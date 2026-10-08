import asyncio
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

sys.path.insert(0, os.getcwd())
from backend.reporting.evaluation_report import assess_run, export_evaluation_report, render_evaluation_report


class EvaluationReportTests(unittest.TestCase):
    def test_all_unresolved_legacy_zero_is_displayed_as_unmeasured(self):
        record = {'independent_judge': {'status': 'partial', 'entity': {
            'total': 280, 'correct': 0, 'incorrect': 0, 'unresolved': 280,
            'accuracy': 0, 'requirement_met': None}}}
        metric = assess_run(record)['metrics'][1]
        self.assertEqual(metric['value'], '未完成核验（280项待核验）')
        self.assertEqual(metric['status'], '待复核')

    def test_twenty_minute_boundary_and_missing_measurement(self):
        for seconds, expected in ((1200, '达标'), (1200.01, '未达标'), (None, '待复核'),
                                  (float('nan'), '待复核'), (-1, '待复核')):
            result = assess_run({'total_duration_seconds': seconds})
            self.assertEqual(result['metrics'][0]['status'], expected)

    def test_heuristic_success_does_not_certify_acceptance(self):
        record = {'total_duration_seconds': 300, 'entity_eval': {'accuracy': 1},
                  'public_url_source_eval': {'support_accuracy': 1}}
        assessment = assess_run(record)
        self.assertFalse(assessment['acceptance_ready'])
        self.assertEqual(assessment['overall'], '待复核')
        self.assertEqual(assessment['metrics'][2]['screening_status'], '初筛达标')
        self.assertIn('至少10轮', render_evaluation_report(record, assessment))

    def test_failed_screening_and_empty_sample_remain_distinct(self):
        result = assess_run({'entity_eval': {'accuracy': .8}})
        self.assertEqual(result['metrics'][1]['screening_status'], '初筛未达标')
        self.assertEqual(result['metrics'][2]['screening_status'], '待补充测评条件')

    def test_entity_screen_uses_all_candidates_and_explains_no_web_samples(self):
        record = {'entity_eval': {'extracted_count': 10, 'auto_evidence_eval': {
            'checked_count': 5, 'auto_evidence_accuracy': 1, 'strict_auto_evidence_accuracy': .5}},
            'public_url_source_eval': {'cited_url_ref_count': 0}}
        result = assess_run(record)
        self.assertEqual(result['metrics'][1]['value'], '50.00%')
        self.assertIn('无链接样本', result['metrics'][2]['value'])
        self.assertNotIn('无法计算', render_evaluation_report(record, result))

    def test_timeout_cannot_be_overridden_by_good_scores(self):
        self.assertEqual(assess_run({'total_duration_seconds': 1500})['overall'], '未达标')

    def test_export_writes_associated_json_and_survives_word_failure(self):
        previous = Path.cwd()
        with TemporaryDirectory() as temporary:
            try:
                os.chdir(temporary)
                with patch('backend.utils.write_md_to_word', new=AsyncMock(side_effect=RuntimeError('failure'))):
                    result = asyncio.run(export_evaluation_report({'run_id': 'test-1'}, '报告_abcd'))
                self.assertEqual(result['word_path'], '')
                self.assertTrue(result['errors'])
                self.assertTrue(Path(unquote(result['md_path'])).is_file())
                saved = json.loads(Path(unquote(result['json_path'])).read_text(encoding='utf-8'))
                self.assertEqual(saved['run_statistics']['run_id'], 'test-1')
                self.assertEqual(saved['assessment']['overall'], '待复核')
            finally:
                os.chdir(previous)
