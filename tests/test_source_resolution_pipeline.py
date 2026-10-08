import unittest
from unittest.mock import patch

from backend.reporting.citations import CitationRegistry
from backend.reporting.finalization import check_contract
from backend.reporting.source_identity import resolve_local_source
from gpt_researcher.evaluation.evidence_samples import saved_pages
from gpt_researcher.evaluation.independent_judge import build_samples
from gpt_researcher.evaluation.source_evaluator import evaluate_public_url_sources


class SourceResolutionPipelineTests(unittest.TestCase):
    def test_bibliography_without_filename_resolves_uniquely(self):
        source = {'file_name': '普惠GTF发动机MRO市场走向_Alex Derber__.pdf',
                  'locator': '普惠GTF发动机MRO市场走向_Alex Derber__.pdf',
                  'title': '普惠GTF发动机MRO市场走向', 'kind': 'local',
                  'pages': [{'page': 1, 'text': 'GTF维修120台。'}]}
        description = 'Derber A. 普惠GTF发动机MRO市场走向[J]. 航空维修与工程, 2023(2): 22-23.'
        record = CitationRegistry('', [source])._record(description)
        self.assertEqual(record['file_name'], source['file_name'])
        self.assertTrue(record['source_id'])
        pages, reason = saved_pages('[原文1]', {'[原文1]': description}, {'sources': [source]})
        self.assertEqual(pages, source['pages'])
        self.assertFalse(reason)

    def test_ambiguous_titles_and_conflicting_filenames_are_not_bound(self):
        a = {'file_name': 'a.pdf', 'title': '普惠发动机市场跟踪研究'}
        b = {'file_name': 'b.pdf', 'title': a['title']}
        self.assertIsNone(resolve_local_source(a['title'] + '[J]', [a, b]))
        self.assertIsNone(resolve_local_source(a['title'] + ' wrong.pdf', [a]))
        self.assertIsNone(resolve_local_source('data.pdf', [a]))
        self.assertIsNone(resolve_local_source('新一代' + a['title'] + '[J]', [a]))

    def test_fetched_web_original_reaches_judge_and_is_reused(self):
        report = '## 分析\nGTF维修120台[URL1]。\n## 证据来源列表\n- [URL1] https://example.com/gtf'
        catalog = {'sources': []}
        with patch('gpt_researcher.evaluation.source_evaluator._read_url_text', return_value='GTF维修120台。') as fetch:
            first = evaluate_public_url_sources(report, source_catalog=catalog)
            second = evaluate_public_url_sources(report, source_catalog=catalog)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(catalog['sources']), 1)
        self.assertTrue(catalog['sources'][0]['source_id'])
        self.assertTrue(first['results'][0]['source_readable'])
        self.assertEqual(first['support_accuracy'], second['support_accuracy'])
        samples = build_samples(report, {'extracted_entities': []}, catalog)
        self.assertTrue(samples[0]['evidence'])
        self.assertEqual(samples[0]['evidence'][0]['text'], 'GTF维修120台。')

    def test_failed_web_fetch_does_not_create_readable_evidence(self):
        catalog = {'sources': []}
        with patch('gpt_researcher.evaluation.source_evaluator._read_url_text', return_value=''):
            result = evaluate_public_url_sources('GTF维修120台[URL1]。\n## 证据来源列表\n- [URL1] https://example.com/a', source_catalog=catalog)
        self.assertEqual(catalog['readable_sources'], 0)
        self.assertEqual(result['unchecked_count'], 1)

    def test_paragraph_end_citation_does_not_cover_previous_sentence(self):
        text = '## 分析\nGTF维修120台。PW1100G维修200台[原文1]。\n## 证据来源列表\n- [原文1] engine.pdf'
        source = {'file_name': 'engine.pdf', 'locator': 'engine.pdf', 'pages': [{'text': 'GTF维修120台。PW1100G维修200台。'}]}
        result = check_contract(text, 'GTF', [source], [source], [], 'research_report')
        missing = [i for i in result['issues'] if i['kind'] == 'missing_sentence_reference']
        self.assertEqual(missing, [])
        fixed = text.replace('GTF维修120台。', 'GTF维修120台[原文1]。')
        self.assertFalse(any(i['kind'] == 'missing_sentence_reference'
                             for i in check_contract(fixed, 'GTF', [source], [source], [], 'research_report')['issues']))
