import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.reporting.reference_metadata import extract_page_metadata, missing_reference_fields
from backend.reporting.reference_lookup import resolve_reference_metadata, _extract_metadata_from_result, _detail_metadata
from backend.reporting.citations import CitationRegistry, _format_gbt_reference
from backend.reporting.formal_report import prepare_formal_report


class ReferenceMetadataTests(unittest.TestCase):
    def test_published_article_url_does_not_change_publication_type(self):
        text = _format_gbt_reference(dict(title='维修研究', author='甲', source_type='论文',
            container='航空动力', year='2026', issue='1', pages='12-17', url='https://doi.org/10.1234/example'))
        self.assertIn('[J].航空动力,2026(1):12-17.', text)
        self.assertNotIn('[J/OL]', text)

    def test_online_first_does_not_require_unassigned_issue_and_pages(self):
        missing = missing_reference_fields(dict(title='维修研究', author='甲', source_type='论文',
            container='航空动力', year='2026', publication_status='online_first', doi='10.1234/example'))
        self.assertEqual(missing, [])

    def test_published_local_article_with_doi_keeps_journal_marker(self):
        reference = dict(title='维修研究', author='张某', source_type='论文',
                         container='航空动力', year='2026', issue='1', pages='12-17', doi='10.1234/example')
        text = _format_gbt_reference(reference)
        self.assertIn('[J].航空动力,2026(1):12-17.', text)
        self.assertNotIn('[J/OL]', text)
        self.assertIn('https://doi.org/10.1234/example', text)
        self.assertIn('[J/OL]', _format_gbt_reference(dict(reference, publication_status='online_first')))

    def test_nonmatching_results_trigger_next_query_and_record_reason(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'REFERENCE_METADATA_CACHE_PATH': str(Path(folder) / 'cache.json')}):
            queries = []
            def search(query, count):
                queries.append(query)
                return [{'title': '另一篇论文', 'url': 'https://example.org/a', 'snippet': ''}]
            result = resolve_reference_metadata(dict(title='航空发动机维修研究', author='张某', container='航空动力', source_type='论文'), searcher=search)
            self.assertEqual(len(queries), 3)
            self.assertIn('航空动力', queries[1])
            self.assertIn('目录', queries[2])
            self.assertEqual(result['lookup_attempts'][0]['candidates'][0]['reason'], 'title_mismatch')
            self.assertEqual(result['lookup_status'], 'no_match')

    def test_retry_selects_complete_metadata_and_rejects_conflict(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'REFERENCE_METADATA_CACHE_PATH': str(Path(folder) / 'cache.json')}):
            def search(query, count):
                return [{'title': '航空发动机维修研究', 'url': 'https://example.org/a', 'snippet': ''}]
            candidates = [
                {'container': '错误刊名', 'lookup_confidence': .99},
                {'container': '航空动力', 'year': '2026', 'lookup_confidence': .95},
                {'container': '航空动力', 'year': '2026', 'issue': '1', 'pages': '12-17', 'lookup_confidence': .95},
            ]
            with patch('backend.reporting.reference_lookup._extract_metadata_from_result', side_effect=candidates):
                result = resolve_reference_metadata(dict(title='航空发动机维修研究', author='张某', container='航空动力', source_type='论文'), searcher=search)
            self.assertEqual(result['lookup_status'], 'matched')
            self.assertEqual(result['pages'], '12-17')
            self.assertEqual(result['lookup_attempts'][0]['candidates'][0]['reason'], 'conflicting_metadata')

    def test_publication_year_is_not_title_year(self):
        pages = [{'page': i + 1, 'text': f'{12+i}\n航空动力 I Aerospace Power 2026年 第1期\n2025年民用航空动力进展'} for i in range(6)]
        result = extract_page_metadata(pages)
        self.assertEqual((result['year'], result['issue'], result['pages']), ('2026', '1', '12-17'))
        self.assertEqual(result['metadata_provenance']['year']['page'], 1)

    def test_unlabelled_body_year_not_publication_year(self):
        self.assertNotIn('year', extract_page_metadata([{'page': 1, 'text': '2025年民用航空动力进展\n2024年投入生产'}]))

    def test_patent_publication_date_and_owner(self):
        text = '(21)申请号202520995988.3\n(22)申请日2025.05.20\n(73)专利权人某航空发动机维\n修有限公司\n地址某市\nCN 224350231 U\n2026.06.12'
        result = extract_page_metadata([{'page': 1, 'text': text}], '专利')
        self.assertEqual(result['patent_number'], 'CN224350231U')
        self.assertEqual(result['publication_date'], '2026-06-12')
        self.assertEqual(result['applicant'], '某航空发动机维修有限公司')
        formatted = _format_gbt_reference(dict(result, title='吊具', author='发明人', source_type='专利'))
        self.assertEqual(formatted, '某航空发动机维修有限公司.吊具: CN224350231U[P].2026-06-12.')

    def test_patent_filename_author_suffix_not_used_as_title(self):
        formatted = _format_gbt_reference({
            'title': '基于多头注意力的发动机寿命预测方法_吕珊珊',
            'description': '基于多头注意力的发动机寿命预测方法_吕珊珊.pdf',
            'file_name': '基于多头注意力的发动机寿命预测方法_吕珊珊.pdf',
            'source_type': '专利',
            'applicant': '河北工业大学',
            'patent_number': 'CN122113574A',
            'publication_date': '2026-05-29',
        })
        self.assertEqual(formatted, '河北工业大学.基于多头注意力的发动机寿命预测方法: CN122113574A[P].2026-05-29.')

    def test_fullwidth_citation_format_metadata_is_extracted(self):
        text = (
            '第47卷 第1期\n兵器装备工程学报\n2026年1月\n'
            'doi:10.11809/bqzbgcxb2026.01.013\n'
            '本文引用格式：李维，陆纪龙，张薇，等．视情维修策略下航空发动机下发期限预测方法［J］．'
            '兵器装备工程学报，2026，47（1）：108－117．'
        )
        result = extract_page_metadata([{'page': 1, 'text': text}], '论文')
        self.assertEqual(result['author'], '李维; 陆纪龙; 张薇; 等')
        self.assertEqual(result['container'], '兵器装备工程学报')
        self.assertEqual(result['year'], '2026')
        self.assertEqual(result['volume_issue_pages'], '47(1):108-117')

    def test_degree_metadata_uses_dissertation_format(self):
        text = (
            '博士学位论文\n民航发动机机群调度优化与视情维修决策方法研究\n'
            '研究生姓名   白  芳\n学科、专业   载运工具运用工程\n'
            '南京航空航天大学\n二ОО 九年五月'
        )
        result = extract_page_metadata([{'page': 1, 'text': text}], '论文')
        self.assertEqual(result['source_type'], '学位论文')
        self.assertEqual(result['author'], '白芳')
        self.assertEqual(result['year'], '2009')
        self.assertEqual(missing_reference_fields(result), [])
        formatted = _format_gbt_reference(result)
        self.assertEqual(formatted, '白芳.民航发动机机群调度优化与视情维修决策方法研究[D].南京航空航天大学,2009.')

    def test_article_number_pages_are_extracted(self):
        text = '2009年 第28卷 1月 第1期\n机械科学与技术\n文章编号:1003-8728(2009)01-0092-06'
        result = extract_page_metadata([{'page': 1, 'text': text}], '论文')
        self.assertEqual(result['container'], '机械科学与技术')
        self.assertEqual(result['volume'], '28')
        self.assertEqual(result['issue'], '1')
        self.assertEqual(result['pages'], '92-97')

    def test_separate_pages_and_issue_format(self):
        result = _format_gbt_reference(dict(title='研究', author='甲', source_type='论文', container='航空动力', year='2026', issue='1', pages='12-17'))
        self.assertEqual(result, '甲.研究[J].航空动力,2026(1):12-17.')

    def test_translator_is_not_coauthor(self):
        result = _format_gbt_reference(dict(title='研究', author='Alex Derber; 文峻', translator='文峻', source_type='论文', container='航空维修与工程', year='2023', issue='2', pages='22-23'))
        self.assertIn('Alex Derber.研究[J].文峻,译.', result)

    def test_existing_structured_metadata_survives_registry(self):
        registry = CitationRegistry('## 参考文献\n[原文1] a.pdf', [dict(file_name='a.pdf', title='研究', author='甲', source_type='论文', container='航空动力', year='2026', issue='1', pages='12-17')])
        registry.convert('论述[原文1]')
        self.assertIn('2026(1):12-17', registry.bibliography())
        self.assertEqual(missing_reference_fields(registry.public['[1]']), [])

    def test_incomplete_reference_reported_even_when_located(self):
        report = prepare_formal_report('# 研究\n## 引言\n内容[原文1]\n## 参考文献\n[原文1] a.pdf', '研究', [dict(file_name='a.pdf', title='研究', author='甲', source_type='论文')])
        self.assertEqual(report.quality['missing_source_ids'], [])
        self.assertIn('刊名', report.quality['reference_metadata_issues'][0]['missing_fields'])
        self.assertNotEqual(report.quality['status'], 'ready')

    def test_long_snippet_does_not_penalize_matching_title(self):
        metadata = _extract_metadata_from_result({'title': '2025年民用航空动力进展'}, {'title': '2025年民用航空动力进展', 'snippet': '其他内容' * 100, 'url': 'https://example.org'})
        self.assertTrue(metadata)
        self.assertEqual(metadata['year'], '')

    def test_negative_cache_expires_and_legacy_empty_cache_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'cache.json'
            with patch.dict('os.environ', {'REFERENCE_METADATA_CACHE_PATH': str(cache)}):
                calls = []
                def search(query, count):
                    calls.append(query)
                    return []
                record = dict(title='航空发动机维修研究', source_type='论文')
                resolve_reference_metadata(record, searcher=search)
                initial = len(calls)
                resolve_reference_metadata(record, searcher=search)
                self.assertEqual(initial, len(calls))
                state = json.loads(cache.read_text(encoding='utf-8'))
                for item in state.values():
                    item['time'] = time.time() - 301
                cache.write_text(json.dumps(state), encoding='utf-8')
                resolve_reference_metadata(record, searcher=search)
                self.assertGreater(len(calls), initial)
                for item in state.values():
                    item.pop('version')
                    item['time'] = time.time()
                cache.write_text(json.dumps(state), encoding='utf-8')
                initial = len(calls)
                resolve_reference_metadata(record, searcher=search)
                self.assertGreater(len(calls), initial)

    def test_search_error_is_not_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'cache.json'
            with patch.dict('os.environ', {'REFERENCE_METADATA_CACHE_PATH': str(cache)}):
                def fail(*args):
                    raise TimeoutError('search timeout')
                result = resolve_reference_metadata(dict(title='航空发动机维修研究'), searcher=fail)
                self.assertEqual(result['lookup_status'], 'error')
                self.assertFalse(cache.exists())

    def test_detail_metadata_title_identity_required(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.is_redirect = False
        response.iter_content.return_value = [b'<meta name="citation_title" content="Engine repair"><meta name="citation_publication_date" content="2026/01"><meta name="citation_journal_title" content="Journal of Engines"><meta name="citation_firstpage" content="12"><meta name="citation_lastpage" content="17">']
        with patch('requests.get', return_value=response):
            result = _detail_metadata('https://www.cnki.net/article', 'Engine repair')
            self.assertEqual(result['year'], '2026')
            self.assertEqual(result['pages'], '12-17')
            self.assertEqual(_detail_metadata('https://www.cnki.net/article', 'Other topic'), {})


if __name__ == '__main__':
    unittest.main()
