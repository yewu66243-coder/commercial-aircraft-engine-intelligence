import unittest

from gpt_researcher.evaluation.evidence_workspace import stage_catalog, evaluation_view
from gpt_researcher.evaluation.review_samples import _evidence, build_samples
from gpt_researcher.evaluation.entity_evaluator import evaluate_report_entities
from gpt_researcher.evaluation.evidence_samples import extract_body_candidates, evaluate_local_references


class EvidenceWorkspaceTests(unittest.TestCase):
    def test_numeric_public_and_local_citations_reach_saved_evidence(self):
        text = '## 分析\nGTF维修120台[[1]](#ref-1)。\nPW1100G维修200台[2]。\n## 参考文献\n[1]网页\n[2]论文'
        mapping = {'[1]': {'original_ids': ['[1]'], 'url': 'https://example.com/gtf'},
                   '[2]': {'original_ids': ['[2]'], 'file_name': 'engine.pdf'}}
        view = evaluation_view(text, mapping)
        catalog = {'sources': [
            {'locator': 'https://example.com/gtf', 'pages': [{'text': 'GTF维修120台。'}]},
            {'locator': 'engine.pdf', 'pages': [{'text': 'PW1100G维修200台。'}]}]}
        self.assertIn('GTF维修120台[URL1]', view)
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

    def test_named_fact_selects_correct_page_instead_of_unrelated_number(self):
        catalog = stage_catalog({'sources': [{'locator': 'test.pdf', 'pages': [
            {'page': 2, 'text': 'LEAP共有10家供应商。'},
            {'page': 4, 'text': 'GTF授权修理厂共有10家，采用封闭式分配。西棕榈滩计划增加40%产能。'}]}]})
        result = _evidence('10家，封闭式分配', ['[原文1]'], {'[原文1]': 'test.pdf'}, catalog, name='GTF授权修理厂')
        self.assertEqual(result[0]['page'], 4)
        self.assertTrue(result[0]['fragment_id'])

    def test_published_body_wins_over_stale_internal_table(self):
        text = '## 1 分析\nGTF供应链最晚要到2030年恢复[[1]](#ref-1)。\n## 参考文献\n[1]test'
        view = evaluation_view(text, {'[1]': {'original_ids': ['[原文1]'], 'file_name': 'test.pdf'}})
        self.assertIn('2030年恢复[原文1]', view)
        catalog = {'sources': [{'locator': 'test.pdf', 'pages': [{'page': 1, 'text': 'GTF供应链最晚要到2030年恢复。'}]}]}
        result = evaluate_report_entities(view, 'GTF', source_catalog=catalog, prefer_body=True)
        self.assertTrue(result['extracted_count'])
        self.assertTrue(all('最晚' in e['value'] for e in result['extracted_entities']))
