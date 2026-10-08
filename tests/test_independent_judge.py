import asyncio
import json
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, os.getcwd())
from gpt_researcher.evaluation.independent_judge import judge_report, summarize, validate_response
from backend.reporting.evaluation_report import assess_run, render_evaluation_report

ENV = {'JUDGE_API_KEY': 'test-private-key', 'JUDGE_BASE_URL': 'https://judge.example/v1', 'JUDGE_MODEL': 'judge-model'}
TEXT = 'GTF发动机2025年维修120台。'
REPORT = '## 1 分析\n' + TEXT + '[原文1]\n\n## 证据来源列表\n- [原文1] engine.pdf'
CATALOG = {'sources': [{'locator': 'engine.pdf', 'pages': [{'page': 2, 'text': TEXT}]}]}
ENTITIES = {'extracted_entities': [{'name': 'GTF', 'value': TEXT, 'evidence': '[原文1]'}]}


class FakeClient:
    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=self)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    async def create(self, **kwargs):
        samples = json.loads(kwargs['messages'][1]['content'])['samples']
        rows = [{'id': s['id'], 'verdict': 'correct', 'reason': '原文明示该实体的数量与时间',
                 'evidence_id': s['evidence'][0]['id'], 'quote': TEXT} for s in samples]
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps({'results': rows})))])


class JudgeTests(unittest.TestCase):
    def test_all_unresolved_is_not_a_measured_zero(self):
        for kind in ('entity', 'source'):
            result = summarize([{'kind': kind, 'name': '[URL1]', 'verdict': 'insufficient'}], kind)
            self.assertIsNone(result['accuracy'])
            self.assertIsNone(result['requirement_met'])
            self.assertEqual(result['coverage'], 0)
            wrong = summarize([{'kind': kind, 'name': '[URL1]', 'verdict': 'incorrect'}], kind)
            self.assertEqual(wrong['accuracy'], 0)
            self.assertFalse(wrong['requirement_met'])

    def run_judge(self, **kwargs):
        return asyncio.run(judge_report(REPORT, ENTITIES, CATALOG, writer_model='writer-model',
                                       client_factory=FakeClient, **kwargs))

    def test_missing_configuration_never_uses_writer_credentials(self):
        result = self.run_judge(env={'OPENAI_API_KEY': 'writer-secret'})
        self.assertEqual(result['status'], 'not_configured')
        self.assertNotIn('writer-secret', str(result))

    def test_disabled_and_same_model(self):
        self.assertEqual(self.run_judge(env={**ENV, 'JUDGE_ENABLED': 'false'})['status'], 'disabled')
        self.assertEqual(self.run_judge(env={**ENV, 'JUDGE_MODEL': 'writer-model'})['status'], 'not_independent')

    def test_invalid_url_and_limits_are_explained_without_secrets(self):
        for changes in ({'JUDGE_BASE_URL': 'https://secret@host/v1'}, {'JUDGE_CONCURRENCY': '0'},
                        {'JUDGE_TOTAL_TIMEOUT_SECONDS': 'nan'}):
            result = self.run_judge(env={**ENV, **changes})
            self.assertEqual(result['status'], 'invalid_config')
            self.assertNotIn('test-private-key', str(result))

    def test_evidence_backed_results_are_counted_and_auditable(self):
        result = self.run_judge(env=ENV)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['entity']['requirement_met'])
        self.assertEqual(result['results'][0]['evidence'][0]['page'], 2)
        self.assertEqual(result['results'][0]['quote'], TEXT)
        self.assertNotIn('test-private-key', json.dumps(result))

    def test_model_cannot_invent_supporting_quote(self):
        batch = [{'id': 'E1', 'evidence': [{'id': 'S1', 'text': TEXT}]}]
        value = {'id': 'E1', 'verdict': 'correct', 'reason': '支持', 'evidence_id': 'S1', 'quote': '不存在的原文内容999台'}
        self.assertEqual(validate_response(json.dumps({'results': [value]}), batch)['E1']['verdict'], 'unverified')
        self.assertEqual(validate_response(json.dumps({'results': [value, value]}), batch), {})

    def test_missing_evidence_remains_in_denominator(self):
        entities = {'extracted_entities': [ENTITIES['extracted_entities'][0], {'name': 'LEAP-1B', 'value': 'LEAP-1B999台', 'evidence': ''}]}
        result = asyncio.run(judge_report(REPORT, entities, CATALOG, env=ENV, client_factory=FakeClient))
        self.assertEqual(result['entity']['total'], 2)
        self.assertEqual(result['entity']['accuracy'], .5)
        self.assertIsNone(result['entity']['requirement_met'])

    def test_sample_cap_does_not_shrink_denominator(self):
        entities = {'extracted_entities': ENTITIES['extracted_entities'] * 2}
        result = asyncio.run(judge_report(REPORT, entities, CATALOG, env={**ENV, 'JUDGE_MAX_ITEMS': '1'}, client_factory=FakeClient))
        self.assertEqual(result['entity']['total'], 2)
        self.assertEqual(result['entity']['unresolved'], 1)

    def test_one_wrong_claim_fails_its_shared_public_reference(self):
        result = summarize([{'kind': 'source', 'name': '[URL1]', 'verdict': v} for v in ['correct', 'incorrect']], 'source')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['incorrect'], 1)
        self.assertFalse(result['requirement_met'])

    def test_public_url_uses_saved_web_original(self):
        report = REPORT.replace('[原文1]', '[URL1]').replace('engine.pdf', 'https://example.org/report')
        catalog = {'sources': [{'locator': 'https://example.org/report', 'kind': 'web', 'pages': [{'page': None, 'text': TEXT}]}]}
        result = asyncio.run(judge_report(report, {}, catalog, env=ENV, client_factory=FakeClient))
        self.assertTrue(result['source']['requirement_met'])

    def test_api_exception_does_not_leak_key_or_fail_report(self):
        async def fail(*args, **kwargs):
            raise RuntimeError('test-private-key')
        with patch.object(FakeClient, 'create', fail):
            result = self.run_judge(env=ENV)
        self.assertEqual(result['status'], 'partial')
        self.assertNotIn('test-private-key', str(result))
        self.assertIsNone(result['entity']['requirement_met'])

    def test_total_timeout_preserves_unresolved_items(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(3)
        with patch.object(FakeClient, 'create', slow):
            result = self.run_judge(env={**ENV, 'JUDGE_TOTAL_TIMEOUT_SECONDS': '1'})
        self.assertIn('TotalTimeout', result['errors'])
        self.assertEqual(result['entity']['unresolved'], 1)

    def test_malformed_or_truncated_response_cannot_pass(self):
        async def bad(*args, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='length', message=SimpleNamespace(content='{}'))])
        with patch.object(FakeClient, 'create', bad):
            result = self.run_judge(env=ENV)
        self.assertEqual(result['entity']['unresolved'], 1)
        self.assertEqual(result['status'], 'partial')

    def test_report_uses_judge_scores_without_certifying_unmeasured_urls(self):
        judge = self.run_judge(env=ENV)
        record = {'total_duration_seconds': 600, 'independent_judge': judge}
        assessment = assess_run(record)
        self.assertEqual(assessment['metrics'][1]['status'], '达标')
        self.assertEqual(assessment['overall'], '待复核')
        self.assertIn('judge-model', render_evaluation_report(record, assessment))

    def test_complete_failed_verdict_changes_report_status(self):
        record = {'total_duration_seconds': 600, 'independent_judge': {
            'status': 'completed', 'model': 'judge-model',
            'entity': {'total': 10, 'correct': 8, 'unresolved': 0, 'accuracy': .8, 'requirement_met': False}}}
        self.assertEqual(assess_run(record)['overall'], '未达标')
