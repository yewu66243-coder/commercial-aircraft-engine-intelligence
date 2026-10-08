import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from gpt_researcher.evaluation.evidence_workspace import stage_catalog, evaluation_view
from gpt_researcher.evaluation.independent_judge import _evidence, judge_report, validate_response
from gpt_researcher.evaluation.entity_evaluator import evaluate_report_entities


class EvidenceWorkspaceTests(unittest.TestCase):
    def test_numeric_public_and_local_citations_reach_saved_evidence(self):
        from gpt_researcher.evaluation.evidence_samples import extract_body_candidates, evaluate_local_references
        from gpt_researcher.evaluation.independent_judge import build_samples
        from gpt_researcher.evaluation.source_evaluator import _extract_url_reference_contexts
        text = '## 分析\nGTF维修120台[[1]](#ref-1)。\nPW1100G维修200台[2]。\n## 参考文献\n[1]网页\n[2]论文'
        mapping = {'[1]': {'original_ids': ['[1]'], 'url': 'https://example.com/gtf'},
                   '[2]': {'original_ids': ['[2]'], 'file_name': 'engine.pdf'}}
        view = evaluation_view(text, mapping)
        catalog = {'sources': [
            {'locator': 'https://example.com/gtf', 'pages': [{'text': 'GTF维修120台。'}]},
            {'locator': 'engine.pdf', 'pages': [{'text': 'PW1100G维修200台。'}]}]}
        self.assertEqual(set(_extract_url_reference_contexts(view)), {'[URL1]'})
        self.assertEqual(evaluate_local_references(view, catalog)['readable_count'], 1)
        samples = build_samples(view, {'extracted_entities': extract_body_candidates(view)}, catalog)
        self.assertTrue(samples)
        self.assertTrue(all(sample['evidence'] for sample in samples))
        self.assertEqual(sum(s['kind'] == 'source' for s in samples), 1)

    def test_original_id_collisions_do_not_merge_distinct_sources(self):
        view = evaluation_view('## 分析\nGTF[1]。PW1100G[2]。', {
            '[1]': {'original_ids': ['[URL2]'], 'url': 'https://example.com/a'},
            '[2]': {'original_ids': ['[URL2]'], 'url': 'https://example.com/b'}})
        self.assertIn('GTF[URL1]。PW1100G[URL2]。', view)
        self.assertIn('- [URL1] https://example.com/a', view)
        self.assertIn('- [URL2] https://example.com/b', view)

    def test_separate_quotes_are_each_checked_against_their_source(self):
        batch = [{'id': 'E1', 'evidence': [{'id': 'S1', 'text': 'GTF维修网络有10家供应商。'},
                                          {'id': 'S2', 'text': '授权修理厂只能按照普惠分配执行维修。'}]}]
        row = {'id': 'E1', 'verdict': 'correct', 'reason': '两处原文共同支持', 'quotes': [
            {'evidence_id': 'S1', 'quote': 'GTF维修网络有10家供应商。'},
            {'evidence_id': 'S2', 'quote': '授权修理厂只能按照普惠分配执行维修。'}]}
        self.assertEqual(validate_response(json.dumps({'results': [row]}), batch)['E1']['verdict'], 'correct')
        row['quotes'][1]['quote'] = '授权修理厂可以自由选择发动机来源。'
        self.assertEqual(validate_response(json.dumps({'results': [row]}), batch)['E1']['verdict'], 'unverified')

    def test_named_fact_selects_correct_page_instead_of_unrelated_number(self):
        catalog = stage_catalog({'sources': [{'locator': 'test.pdf', 'pages': [
            {'page': 2, 'text': 'LEAP共有10家供应商。'},
            {'page': 4, 'text': 'GTF授权修理厂共有10家，采用封闭式分配。西棕榈滩计划增加40%产能。'}]}]})
        result = _evidence('10家，封闭式分配', ['[原文1]'], {'[原文1]': 'test.pdf'},
                           catalog, name='GTF授权修理厂')
        self.assertEqual(result[0]['page'], 4)
        self.assertTrue(result[0]['fragment_id'])

    def test_joined_sentences_require_every_sentence_to_match(self):
        batch = [{'id': 'E1', 'evidence': [{'id': 'S1', 'text':
            'GTF维修网络有10家供应商。这里有一些无关内容。授权修理厂按照普惠分配维修。'}]}]
        row = {'id': 'E1', 'verdict': 'correct', 'reason': '支持', 'evidence_id': 'S1',
               'quote': 'GTF维修网络有10家供应商。授权修理厂按照普惠分配维修。'}
        validated = validate_response(json.dumps({'results': [row]}), batch)['E1']
        self.assertEqual(validated['verdict'], 'correct')
        self.assertEqual(len(validated['quotes']), 2)
        row['quote'] += '每年保证维修10000台。'
        self.assertEqual(validate_response(json.dumps({'results': [row]}), batch)['E1']['verdict'], 'unverified')

    def test_published_body_wins_over_stale_internal_table(self):
        text = '## 1 分析\nGTF供应链最晚要到2030年恢复[[1]](#ref-1)。\n## 参考文献\n[1]test'
        view = evaluation_view(text, {'[1]': {'original_ids': ['[原文1]'], 'file_name': 'test.pdf'}})
        self.assertIn('2030年恢复[原文1]', view)
        self.assertNotIn('#ref-', view)
        catalog = {'sources': [{'locator': 'test.pdf', 'pages': [{'page': 1, 'text': 'GTF供应链最晚要到2030年恢复。'}]}]}
        result = evaluate_report_entities(view, 'GTF', source_catalog=catalog, prefer_body=True)
        self.assertTrue(result['extracted_count'])
        self.assertTrue(all('最晚' in e['value'] for e in result['extracted_entities']))

    def test_retry_preserves_first_verdict_and_saved_fragments(self):
        text = 'GTF发动机2025年维修120台。'
        class Client:
            calls = 0
            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=self)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def create(self, **kwargs):
                Client.calls += 1
                sample = json.loads(kwargs['messages'][1]['content'])['samples'][0]
                row = {'id': sample['id'], 'verdict': 'correct', 'reason': '支持',
                       'evidence_id': 'S1', 'quote': '无法定位的虚构原文内容' if Client.calls == 1 else text}
                return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
                    message=SimpleNamespace(content=json.dumps({'results': [row]})))])
        catalog = stage_catalog({'sources': [{'locator': 'test.pdf', 'pages': [{'page': 4, 'text': text}]}]})
        report = '## 分析\n' + text + '[原文1]\n## 证据来源列表\n- [原文1] test.pdf'
        entities = {'extracted_entities': [{'name': 'GTF', 'value': text, 'evidence': '[原文1]'}]}
        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(judge_report(report, entities, catalog, workspace=directory, client_factory=Client,
                env={'JUDGE_API_KEY': 'test', 'JUDGE_BASE_URL': 'https://example.com/v1', 'JUDGE_MODEL': 'judge'}))
            self.assertEqual(Client.calls, 2)
            self.assertEqual(result['entity']['correct'], 1)
            attempts = result['results'][0]['attempts']
            self.assertEqual([a['verdict'] for a in attempts], ['unverified', 'correct'])
            self.assertEqual(attempts[0]['evidence'][0]['selection'], 'saved_fragment')
            self.assertEqual(attempts[1]['evidence'][0]['selection'], 'expanded_original')
            self.assertTrue((Path(directory) / 'judge_results.json').is_file())
