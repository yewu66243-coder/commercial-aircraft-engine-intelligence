import asyncio
import copy
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend.reporting.source_grounding import build_source_catalog, pack_sources
from backend.reporting.web_source_tracking import build_web_source_tracking
from gpt_researcher.retrievers.web_evidence_policy import build_topic_queries, evidence_assessment
from gpt_researcher.skills.researcher import ResearchConductor


class WebSourcePipelineTests(unittest.TestCase):
    def page(self, index, text=None, title='GTF maintenance'):
        return {'url':f'https://example.com/article/{index}', 'title':title,
                'raw_content':text or 'GTF engine maintenance capacity. ' * 80}

    def catalog(self, pages, **kwargs):
        return build_source_catalog([], [{'web_evidence':pages}], allow_web=True,
                                    allow_fetch=False, query='GTF 发动机维修', **kwargs)

    def test_all_bodies_survive_deduplication_and_no_twenty_record_cap(self):
        pages = [self.page(i) for i in range(45)]
        pages += [self.page(44, 'GTF' + '原文' * 25000)]
        catalog = self.catalog(pages)
        self.assertEqual(len(catalog['sources']), 45)
        self.assertEqual(catalog['sources'][-1]['pages'][0]['text'], pages[-1]['raw_content'])

    def test_failed_candidates_preserved_without_treating_snippets_as_text(self):
        section = {'web_candidates':[{'url':'https://example.com/failed', 'title':'GTF',
                                     'snippet':'GTF relevant search snippet ' * 50,
                                     'fetch_status':'failed', 'fetch_reason':'未取得正文', 'query':'GTF'}]}
        with patch('gpt_researcher.evaluation.entity_evaluator._read_url_text') as fetch:
            catalog = build_source_catalog([], [section], allow_web=True, query='GTF')
        fetch.assert_not_called()
        row = build_web_source_tracking(catalog)['sources'][0]
        self.assertEqual(row['fetch_status'], 'failed')
        self.assertEqual(row['raw_characters'], 0)
        self.assertEqual(row['queries'], ['GTF'])

    def test_challenge_and_unrelated_pages_archived_but_not_used(self):
        catalog = self.catalog([self.page(1, '请完成验证码验证 ' * 20, '验证-道客巴巴'),
                                self.page(2, '电子元器件 PLC 产品规格书 ' * 50, 'PLC'), self.page(3)])
        packed = pack_sources(catalog, 'GTF', budget=8000)
        self.assertEqual(len(catalog['sources']), 3)
        self.assertNotIn('/article/1', packed)
        self.assertNotIn('/article/2', packed)
        self.assertIn('/article/3', packed)

    def test_repeated_candidate_does_not_erase_an_observed_failure(self):
        section = {'web_candidates':[
            {'url':'https://example.com/a', 'fetch_status':'failed', 'fetch_reason':'抓取失败'},
            {'url':'https://example.com/a', 'fetch_status':'not_attempted', 'fetch_reason':'重复搜索命中'},
        ]}
        catalog = build_source_catalog([], [section], allow_web=True, allow_fetch=False, query='GTF')
        self.assertEqual(catalog['sources'][0]['fetch_status'], 'failed')
        self.assertEqual(catalog['sources'][0]['fetch_reason'], '抓取失败')

    def test_later_evaluation_fetch_updates_the_original_failure_status(self):
        from gpt_researcher.evaluation.source_evaluator import evaluate_public_url_sources
        catalog = build_source_catalog([], [{'web_candidates':[
            {'url':'https://example.com/gtf', 'fetch_status':'failed'}]}],
            allow_web=True, allow_fetch=False, query='GTF')
        report = 'GTF维修120台[URL1]。\n## 证据来源列表\n- [URL1] https://example.com/gtf'
        with patch('gpt_researcher.evaluation.source_evaluator._read_url_text', return_value='GTF维修120台。' * 30):
            evaluate_public_url_sources(report, source_catalog=catalog)
        row = build_web_source_tracking(catalog)['sources'][0]
        self.assertEqual(row['fetch_status'], 'success')
        self.assertTrue(row['evidence_eligible'])

    def test_ranking_finds_late_source_and_relevant_passage_within_budget(self):
        pages = [self.page(i, 'GTF background ' * 80, 'Background') for i in range(25)]
        pages += [self.page(26, 'unrelated introduction ' * 500 + 'GTF maintenance capacity ' * 100)]
        catalog = self.catalog(pages)
        packed = pack_sources(catalog, 'GTF maintenance capacity', budget=1500)
        self.assertIn('/article/26', packed)
        self.assertIn('GTF maintenance capacity', packed)
        self.assertLessEqual(len(packed), 1500)
        for fragment in catalog['writing_fragments'].values():
            original = next(p['raw_content'] for p in pages if p['url'] == fragment['source'])
            self.assertIn(fragment['text'], original)

    def test_review_does_not_overwrite_writing_selection(self):
        catalog = self.catalog([self.page(i) for i in range(4)])
        pack_sources(catalog, 'GTF', budget=1500)
        selection = [s['writing_selected'] for s in catalog['sources']]
        fragments = copy.deepcopy(catalog['writing_fragments'])
        pack_sources(catalog, 'GTF', budget=20000, purpose='review')
        self.assertEqual(selection, [s['writing_selected'] for s in catalog['sources']])
        self.assertEqual(fragments, catalog['writing_fragments'])

    def test_dates_prioritize_recent_but_do_not_invent_unknown_dates(self):
        args = ('GTF', 'GTF maintenance', 'GTF maintenance ' * 100)
        old = evidence_assessment(*args, published_date='2017-01-01', today=date(2026, 9, 29))
        recent = evidence_assessment(*args, published_date='2026-09-01', today=date(2026, 9, 29))
        unknown = evidence_assessment(*args, url='https://example.com/2026/09/01/article')
        self.assertGreater(recent['relevance_score'], old['relevance_score'])
        self.assertTrue(old['evidence_eligible'])
        self.assertEqual(unknown['recency'], '发布日期未知')

    def test_short_bilingual_queries_respect_topic_and_do_not_copy_long_prompt(self):
        task = 'GTF PW1100G PW1500G 维修、适航、技术、市场跟踪'
        queries = build_topic_queries(task, task + '。围绕“适航安全与监管”开展检索；' + '问题描述' * 50,
                                      ['rtx.com', 'faa.gov'])
        self.assertTrue(all(len(q) < 120 for q in queries))
        self.assertIn('适航指令', queries[0])
        self.assertIn('airworthiness', queries[1])
        self.assertIn('site:faa.gov', queries[-1])
        self.assertNotIn('GTF', ' '.join(build_topic_queries('LEAP-1A 技术', '技术')))
        self.assertEqual(build_topic_queries('未识别主题', '主题'), [])

    def test_tracking_counts_body_citations_only_and_explains_nonuse(self):
        catalog = self.catalog([self.page(i) for i in range(3)])
        pack_sources(catalog, 'GTF', budget=1500)
        citation_map = {'[1]':{'url':self.page(0)['url']}, '[2]':{'url':self.page(1)['url']}}
        tracking = build_web_source_tracking(catalog, citation_map, '正文[1]\n## 参考文献\n[1] 来源\n[2] 未引用')
        self.assertEqual(tracking['summary']['cited'], 1)
        self.assertEqual(tracking['summary']['fetched'], 3)
        self.assertTrue(all(r['reason'] for r in tracking['sources']))


class WebConductorTests(unittest.IsolatedAsyncioTestCase):
    def conductor(self):
        researcher = SimpleNamespace(query='GTF', evidence_task='GTF', retrievers=[], verbose=False,
                                     cfg=SimpleNamespace(max_search_results_per_query=5),
                                     visited_urls=set(), vector_store=None, report_type='research_report',
                                     add_research_sources=Mock(), scraper_manager=SimpleNamespace(browse_urls=AsyncMock()))
        return ResearchConductor(researcher)

    async def test_compact_plan_is_used_without_reintroducing_full_query(self):
        conductor = self.conductor()
        conductor.researcher.compact_web_queries = ['GTF 维修', 'GTF maintenance']
        conductor._process_sub_query = AsyncMock(return_value='context')
        conductor._get_mcp_strategy = Mock(return_value='disabled')
        with patch('gpt_researcher.skills.researcher.get_search_results') as initial:
            await conductor._get_context_by_web_search('冗长完整任务')
        initial.assert_not_called()
        self.assertEqual([c.args[0] for c in conductor._process_sub_query.call_args_list], ['GTF 维修', 'GTF maintenance'])

    async def test_fetch_ledger_captures_failures_and_screens_before_context(self):
        conductor = self.conductor()
        pages = [{'url':'https://example.com/good', 'raw_content':'GTF maintenance ' * 100},
                 {'url':'https://example.com/captcha', 'title':'验证-道客巴巴', 'raw_content':'验证码 ' * 50}]
        urls = [p['url'] for p in pages] + ['https://example.com/failed']
        conductor.researcher.web_candidates = [{'url':u} for u in urls]
        conductor._search_relevant_source_urls = AsyncMock(return_value=(urls, []))
        conductor.researcher.scraper_manager.browse_urls.return_value = pages
        result = await conductor._scrape_data_by_urls('GTF maintenance')
        self.assertEqual(len(result), 1)
        self.assertEqual(len(conductor.researcher.saved_web_evidence), 2)
        self.assertEqual(conductor.researcher.web_candidates[-1]['fetch_status'], 'failed')

    async def test_open_queries_do_not_inherit_builtin_domain_wall(self):
        conductor = self.conductor()
        retriever = Mock()
        retriever.__name__ = 'TestRetriever'
        retriever.return_value.search.return_value = [{'href':'https://example.com/1', 'title':'GTF'}]
        conductor.researcher.retrievers = [retriever]
        conductor.researcher.compact_web_queries = ['GTF maintenance']
        conductor.researcher.compact_query_domains = []
        await conductor._search_relevant_source_urls('GTF maintenance', ['faa.gov'])
        retriever.assert_called_with('GTF maintenance', query_domains=[])
        self.assertEqual(len(conductor.researcher.web_candidates), 1)
        conductor.researcher.compact_query_domains = ['example.com']
        await conductor._search_relevant_source_urls('GTF maintenance', ['faa.gov'])
        retriever.assert_called_with('GTF maintenance', query_domains=['example.com'])
