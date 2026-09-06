"""Evidence transport and bounded content-enrichment regression checks."""
import asyncio
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def report(paragraph='可靠性需要结合部件范围和维护条件分析。', copies=1):
    return ('# 研究报告\n\n## 摘要\n摘要。\n\n## 1 引言\n研究范围。\n\n'
            '## 2 资料来源与研究方法\n使用已有资料。\n\n'
            '## 3 技术机制\n' + '\n\n'.join(f'{i}：{paragraph}[URL1]' for i in range(copies)) +
            '\n\n## 4 运营影响\n' + paragraph + '[URL1]\n\n'
            '## 5 综合讨论与研究局限\n统计时间不同，不能直接合并。\n\n'
            '## 6 结论与建议\n应结合实际检查范围分析。\n\n'
            '## 证据来源列表\n- [URL1] 机构. 通告. https://example.org/source\n')


class ContentReviewTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('backend.reporting.content_depth'),
                             'Content depth checks and evidence transport are missing')
        return importlib.import_module('backend.reporting.content_depth')

    def test_evidence_budget_preserves_each_research_topic_and_locator(self):
        module = self.module()
        sections = [{'title': f'研究{i}', 'context': [f'专题{i}的原始摘录。' * 800],
                     'sources': [{'url': f'https://example.org/{i}', 'title': f'原始材料{i}',
                                  'raw_content': f'专题{i}的具体数据。' * 800}]}
                    for i in range(4)]
        packed = module.pack_evidence(sections, budget=6000)
        self.assertLessEqual(len(packed['text']), 6000)
        for i in range(4):
            self.assertIn(f'专题{i}', packed['text'])
            self.assertIn(f'https://example.org/{i}', packed['text'])
        self.assertTrue(packed['truncated'])

    def test_many_candidates_do_not_reduce_every_source_to_an_empty_fragment(self):
        module = self.module()
        sources = [{'title': '来源说明' * 8, 'url': f'https://example.org/article/{i}', 'raw_content': '具体研究证据段落。' * 300} for i in range(100)]
        packed = module.pack_evidence([{'sources':sources,'context':['命中的相关段落。' * 100]}], budget=2400)
        self.assertIn('https://example.org/article/', packed['text'])
        self.assertIn('命中的相关段落', packed['text'])
        self.assertIn('具体研究证据段落', packed['text'])
        self.assertTrue(packed['truncated'])
        self.assertLessEqual(len(packed['text']), 2400)

    def test_reference_bulk_cannot_make_a_short_report_pass(self):
        module = self.module()
        text = report() + '\n' + '参考资料标题和网址。' * 1800
        result = module.review_content(text, 'detailed_report')
        self.assertLess(result['body_characters'], 400)
        self.assertTrue(result['needs_enrichment'])
        self.assertEqual(result['thematic_sections'], 2)

    def test_unresolved_source_placeholders_are_reported_in_content_review(self):
        module = self.module()
        result=module.review_content(report().replace('[URL1]', '[URL?]', 1))
        self.assertGreater(result.get('unresolved_placeholders',0),0)

    def test_repeated_paragraphs_do_not_count_as_substance(self):
        module = self.module()
        original = report()
        repeated = original.replace('## 4 运营影响', ('可靠性需要结合部件范围和维护条件分析。[URL1]\n\n' * 300) + '## 4 运营影响')
        result = module.review_content(repeated, 'research_report')
        self.assertGreater(result['duplicate_paragraphs'], 200)
        self.assertLess(result['body_characters'], 400)

    def test_revision_must_keep_sources_and_existing_chapters(self):
        module = self.module()
        original = report()
        expanded = report('可靠性需要结合部件范围和维护条件分析，统计数据来自同一公告时点。', copies=4)
        self.assertTrue(module.usable_revision(original, expanded))
        self.assertFalse(module.usable_revision(original, expanded.replace('https://example.org/source', 'https://example.org/other')))
        self.assertFalse(module.usable_revision(original, expanded.replace('## 4 运营影响', '## 4 其他内容')))

    def test_revision_cannot_swap_source_ids_even_if_both_urls_remain(self):
        module = self.module()
        original = report().replace('## 4 运营影响\n', '## 4 运营影响\n具体记录[URL2]。\n\n') + '- [URL2] 机构. 记录. https://example.org/second\n'
        swapped = original.replace('https://example.org/source', 'https://example.org/tmp').replace('https://example.org/second', 'https://example.org/source').replace('https://example.org/tmp', 'https://example.org/second')
        self.assertFalse(module.usable_revision(original, swapped))

    def test_bibliography_labels_cannot_replace_body_citations(self):
        module = self.module()
        original = report()
        body, refs = original.split('## 证据来源列表', 1)
        self.assertFalse(module.usable_revision(original, body.replace('[URL1]', '') + '## 证据来源列表' + refs))

    def test_growth_elsewhere_cannot_hide_an_emptied_chapter(self):
        module = self.module()
        original = report()
        expanded = report('可靠性需要结合部件范围和维护条件分析。' * 20, copies=3)
        import re
        emptied = re.sub(r'(## 3 技术机制\n).*?(?=## 4)', r'\1\n', expanded, flags=re.S)
        self.assertFalse(module.usable_revision(original, emptied))

    def test_local_reference_uses_unique_known_title_to_restore_exact_filename(self):
        module = self.module()
        sources=[{'title':'发动机年度进展_张三','author':'张三','file_name':'发动机年度进展_张三.pdf'}]
        original='正文[原文1]。\n\n## 证据来源列表\n- [原文1] 张三. 发动机年度进展. 本地PDF.\n'
        bound=module.bind_local_source_filenames(original,sources)
        self.assertIn('[原文1] 发动机年度进展_张三.pdf', bound)
        from backend.reporting.citations import CitationRegistry
        registry = CitationRegistry(bound, sources)
        registry.convert('正文[原文1]。')
        self.assertFalse(registry.missing)
        self.assertEqual(module.bind_local_source_filenames(bound,sources),bound)
        ambiguous=sources+[{'title':'发动机年度进展','file_name':'另一版本.pdf'}]
        self.assertEqual(module.bind_local_source_filenames(original,ambiguous),original)
        wrong_file=original.replace('本地PDF.','不匹配文件.pdf')
        self.assertEqual(module.bind_local_source_filenames(wrong_file,sources),wrong_file)
        other_title='## 证据来源列表\n- [原文1] Smith. GTF reliability study in hostile environments. 2026.\n'
        short_title=[{'title':'GTF reliability study','file_name':'GTF reliability study.pdf'}]
        self.assertEqual(module.bind_local_source_filenames(other_title,short_title),other_title)

    def test_planner_covers_more_topics_in_detailed_mode(self):
        from three_agent_service import ThreeAgentRequestData, ThreeAgentService
        service = ThreeAgentService(ThreeAgentRequestData(task='GTF技术和市场', report_type='detailed_report'))
        service.demand_profile = {'matched_topics': [{'name': f'主题{i}', 'priority_questions': [f'问题{i}']} for i in range(8)]}
        topics = service.planner_agent()
        self.assertEqual(len(topics), 6)
        self.assertIn('主题5', topics[-1])

    def test_research_returns_context_and_sources_for_synthesis(self):
        from three_agent_service import ThreeAgentRequestData, ThreeAgentService
        researcher = SimpleNamespace(cfg=SimpleNamespace(), conduct_research=AsyncMock(),
                                     write_report=AsyncMock(return_value='研究稿'),
                                     get_research_context=lambda: ['原始段落'],
                                     get_research_sources=lambda: [{'url': 'https://example.org/source', 'raw_content': '证据原文'}])
        with patch('three_agent_service.GPTResearcher', return_value=researcher):
            result = asyncio.run(ThreeAgentService(ThreeAgentRequestData(task='GTF'))._research_one('技术', 1))
        self.assertEqual(result.get('context'), ['原始段落'])
        self.assertEqual(result.get('sources'), researcher.get_research_sources())


class EnrichmentFlowTests(unittest.TestCase):
    def run_writer(self, second, first=None, first_reason='stop', second_reason='stop'):
        from three_agent_service import ThreeAgentRequestData, ThreeAgentService
        first = first or report()
        response = lambda text, reason='stop': SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=reason)])
        calls = AsyncMock(side_effect=[response(first, first_reason), second if isinstance(second, Exception) else response(second, second_reason)])
        service = ThreeAgentService(ThreeAgentRequestData(task='GTF研究'))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=calls)))
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'offline-test-key'}), patch('three_agent_service.AsyncOpenAI', return_value=client):
            result = asyncio.run(service.writer_agent([{'title': '技术', 'subtopic': 'GTF', 'draft': '已取得的来源研究稿', 'context': ['原文摘录']}]))
        return result, service, calls

    def test_one_enrichment_attempt_uses_evidence_and_keeps_complete_first_draft_on_failure(self):
        result, service, calls = self.run_writer(RuntimeError('temporary unavailable'))
        self.assertEqual(calls.await_count, 2)
        self.assertEqual(result, report())
        self.assertEqual(service.generation_status, 'ready')
        self.assertGreaterEqual(calls.call_args_list[0].kwargs.get('max_tokens', 0), 8192)
        self.assertIn('原文摘录', calls.call_args_list[0].kwargs['messages'][1]['content'])
        self.assertFalse(service.content_enrichment['accepted'])

    def test_longer_revision_is_accepted_and_attempts_are_bounded(self):
        expanded = report('可靠性需要结合部件范围和维护条件分析，统计数据来自同一公告时点。', copies=4)
        result, service, calls = self.run_writer(expanded)
        self.assertEqual(result, expanded)
        self.assertEqual(calls.await_count, 2)
        self.assertTrue(service.content_enrichment['accepted'])

    def test_source_loss_rejects_revision(self):
        result, service, calls = self.run_writer(report(copies=8).replace('https://example.org/source', 'https://example.org/other'))
        self.assertEqual(result, report())
        self.assertEqual(calls.await_count, 2)
        self.assertFalse(service.content_enrichment['accepted'])

    def test_initial_truncation_is_a_preserved_draft(self):
        result, service, calls = self.run_writer('', first_reason='length')
        self.assertEqual(result, report())
        self.assertEqual(service.generation_status, 'draft')
        self.assertEqual(calls.await_count, 1)

    def test_truncated_revision_keeps_complete_first_draft(self):
        result, service, calls = self.run_writer(report(copies=8), second_reason='length')
        self.assertEqual(result, report())
        self.assertEqual(service.generation_status, 'ready')
        self.assertFalse(service.content_enrichment['accepted'])

    def test_adequately_developed_report_does_not_trigger_extra_model_call(self):
        complete = report('具体型号与部件范围应依据同一批次记录，维修时间取决于实际进厂和出厂日期，并须区分计划与实现条件。' * 15, copies=7)
        result, service, calls = self.run_writer(RuntimeError('unexpected second call'), first=complete)
        self.assertEqual(result, complete)
        self.assertEqual(calls.await_count, 1)
        self.assertFalse(service.content_enrichment['attempted'])


if __name__ == '__main__':
    unittest.main()
