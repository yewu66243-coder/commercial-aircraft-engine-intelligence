import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.reporting.body_citations import (
    citation_coverage, repair_sources, repair_packet, apply_citation_bindings, repair_body_citations,
)
from backend.reporting.formal_report import prepare_formal_report
from backend.reporting.finalization import usable_edit, check_contract


FACT = '2025年GTF发动机维修产能同比提高35%，新增工位投入使用，维修企业同时扩大技术人员培训。'
BODY = FACT + '产能提高与维修周期变化应分开比较，计划值不能当作实际完成值。'
SOURCE = {'kind':'web', 'locator':'https://example.com/gtf', 'title':'GTF维修产能',
          'pages':[{'page':None, 'text':FACT + '来源正文补充说明。' * 30}]}
LOCAL = {'kind':'local', 'locator':'图源.pdf', 'file_name':'图源.pdf',
         'pages':[{'page':1, 'text':'这是一张发动机示意图。'}]}


def report(body=BODY, cited=False, internal=True):
    result = ('# GTF研究\n\n## 摘要\n' + '摘要说明。' * 70 + '\n\n**关键词：** GTF；维修；产能\n\n'
              '## 1 维修产能\n' + body + ('[URL1]' if cited else '') + '\n\n'
              '## 2 综合讨论\n分析应结合机队实际运行情况，区分计划目标与实际完成量。\n\n'
              '## 3 结论与建议\n后续继续跟踪维修周期变化。\n\n'
              '## 证据来源列表\n- [URL1] 机构. GTF维修产能. https://example.com/gtf\n')
    if internal:
        result += '\n## 核心实体与参数清单（内部核验）\n| 产能 | 35% | [URL1] |\n'
    return result


class CitationCoverageTests(unittest.TestCase):
    def test_internal_references_do_not_count_as_body_citations(self):
        check = citation_coverage(report(), [], [SOURCE])
        self.assertFalse(check['passed'])
        self.assertEqual(check['cited_paragraph_count'], 0)
        self.assertEqual(check['uncited_fact_paragraph_count'], 1)
        self.assertTrue(any(i['kind'] == 'missing_body_citations' for i in check['issues']))

    def test_one_reference_can_cover_related_sentences_in_one_paragraph(self):
        self.assertTrue(citation_coverage(report(cited=True), [], [SOURCE])['passed'])
        self.assertFalse(any(i['kind'] == 'missing_sentence_reference' for i in
                             check_contract(report(cited=True), 'GTF', [], [SOURCE], [], 'research_report')['issues']))

    def test_figure_reference_does_not_mask_missing_prose_citations(self):
        text = report().replace('## 2 综合讨论', '![示意图](image.png)\n\n图源：图源.pdf，第1页。[原文: 图源.pdf]\n\n## 2 综合讨论')
        check = citation_coverage(text, [LOCAL], [SOURCE, LOCAL])
        self.assertEqual(check['cited_paragraph_count'], 0)
        prepared = prepare_formal_report(text, 'GTF', [LOCAL], metadata={'source_catalog':{'sources':[SOURCE,LOCAL]}})
        self.assertEqual(prepared.quality['reference_count'], 1)
        self.assertFalse(prepared.quality['body_citation_coverage']['passed'])
        self.assertEqual(prepared.quality['status'], 'needs_review')

    def test_unknown_and_rejected_sources_do_not_satisfy_linkage(self):
        self.assertFalse(citation_coverage(report(cited=True).replace('[URL1]', '[URL99]', 1), [], [SOURCE])['passed'])
        self.assertFalse(citation_coverage(report(cited=True), [], [{**SOURCE,'evidence_eligible':False}])['passed'])

    def test_direct_link_and_local_filename_are_recognized(self):
        self.assertTrue(citation_coverage(report(body=BODY+'[报告](https://example.com/gtf)'), [], [SOURCE])['passed'])
        self.assertTrue(citation_coverage(report(body=BODY+'[原文: 图源.pdf]'), [LOCAL], [LOCAL])['passed'])

    def test_nested_internal_headings_remain_excluded(self):
        text = report(cited=True) + '\n### 待核验数据\n2026年GTF交付200台。\n'
        self.assertTrue(citation_coverage(text, [], [SOURCE])['passed'])

    def test_editor_cannot_move_all_references_to_internal_sections(self):
        self.assertFalse(usable_edit(report(cited=True), report()))
        self.assertFalse(usable_edit(report(cited=True), report(cited=True).replace('https://example.com/gtf', 'https://wrong.example/other')))


class CitationRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_writer_service_invokes_repair_and_retains_audit(self):
        from three_agent_service import ThreeAgentService, ThreeAgentRequestData
        async def respond(**kwargs):
            prompt = kwargs['messages'][-1]['content']
            if '材料：\n' in prompt:
                packet = json.loads(prompt.split('材料：\n')[1])
                content = json.dumps({'bindings':[{'id':packet['paragraphs'][0]['id'], 'supports_paragraph':True,
                    'evidence':[{'passage_id':packet['passages'][0]['passage_id'], 'quote':FACT}]}]})
            else:
                content = report()
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason='stop')])
        create = AsyncMock(side_effect=respond)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.dict('os.environ', {'OPENAI_API_KEY':'test'}, clear=True), \
             patch('three_agent_service.AsyncOpenAI', return_value=client), \
             patch('three_agent_service.review_content', return_value={'needs_enrichment':False}):
            service = ThreeAgentService(ThreeAgentRequestData(task='GTF维修研究'))
            service.source_catalog = {'sources':[SOURCE], 'errors':[]}
            repaired = await service.writer_agent([])
        self.assertEqual(create.await_count, 2)
        self.assertIn(BODY+'[URL1]', repaired)
        self.assertEqual(service.content_enrichment['body_citation_repair']['status'], 'linked_pending_fact_review')

    async def test_valid_original_quote_adds_only_citation_and_export_keeps_web_reference(self):
        async def complete(prompt):
            packet = json.loads(prompt.split('材料：\n')[1])
            return json.dumps({'bindings':[{'id':packet['paragraphs'][0]['id'], 'supports_paragraph':True,
                'evidence':[{'passage_id':packet['passages'][0]['passage_id'], 'quote':FACT}]}]})
        original = report()
        repaired, audit = await repair_body_citations(original, [], {'sources':[SOURCE]}, complete)
        self.assertEqual(repaired, original.replace(BODY, BODY+'[URL1]'))
        self.assertEqual(audit['status'], 'linked_pending_fact_review')
        self.assertTrue(audit['after']['passed'])
        prepared = prepare_formal_report(repaired, 'GTF', metadata={'source_catalog':{'sources':[SOURCE]}})
        self.assertEqual(prepared.quality['reference_count'], 1)
        self.assertEqual(prepared.citation_map['[1]']['url'], SOURCE['locator'])

    async def test_invented_quote_is_rejected_and_original_remains_unchanged(self):
        async def complete(prompt):
            packet = json.loads(prompt.split('材料：\n')[1])
            return json.dumps({'bindings':[{'id':packet['paragraphs'][0]['id'], 'supports_paragraph':True,
                'evidence':[{'passage_id':packet['passages'][0]['passage_id'], 'quote':'伪造的很长原文，产能提高百分之九十九。'}]}]})
        repaired, audit = await repair_body_citations(report(), [], {'sources':[SOURCE]}, complete)
        self.assertEqual(repaired, report())
        self.assertEqual(audit['status'], 'needs_review')
        self.assertTrue(audit['rejected'])

    async def test_no_model_call_if_linkage_already_present(self):
        complete = AsyncMock()
        repaired, audit = await repair_body_citations(report(cited=True), [], {'sources':[SOURCE]}, complete)
        complete.assert_not_awaited()
        self.assertEqual(audit['status'], 'not_needed')

    async def test_failure_is_audited_without_secrets_or_fabricated_citations(self):
        complete = AsyncMock(side_effect=RuntimeError('secret-credential'))
        repaired, audit = await repair_body_citations(report(), [], {'sources':[SOURCE]}, complete)
        self.assertEqual(repaired, report())
        self.assertEqual(audit['status'], 'needs_review')
        self.assertNotIn('secret-credential', str(audit))

    async def test_no_original_does_not_use_entity_table_as_evidence(self):
        complete = AsyncMock()
        repaired, audit = await repair_body_citations(report(), [], {'sources':[]}, complete)
        complete.assert_not_awaited()
        self.assertEqual(repaired, report())
        self.assertEqual(audit['status'], 'needs_review')

    async def test_table_citations_do_not_break_markdown_cells(self):
        table = '| 对象 | 数据 |\n| --- | --- |\n| GTF | 2025年维修产能提高35% |'
        text = report(body=table)
        async def complete(prompt):
            packet = json.loads(prompt.split('材料：\n')[1])
            return json.dumps({'bindings':[{'id':packet['paragraphs'][0]['id'], 'supports_paragraph':True,
                'evidence':[{'passage_id':packet['passages'][0]['passage_id'], 'quote':FACT}]}]})
        repaired, audit = await repair_body_citations(text, [], {'sources':[SOURCE]}, complete)
        self.assertIn(table+'\n\n表格来源：[URL1]', repaired)
        self.assertTrue(audit['after']['passed'])

    async def test_new_source_definition_is_added_only_when_bound(self):
        text = report().replace('- [URL1] 机构. GTF维修产能. https://example.com/gtf', '')
        async def complete(prompt):
            packet = json.loads(prompt.split('材料：\n')[1])
            return json.dumps({'bindings':[{'id':packet['paragraphs'][0]['id'], 'supports_paragraph':True,
                'evidence':[{'passage_id':packet['passages'][0]['passage_id'], 'quote':FACT}]}]})
        repaired, audit = await repair_body_citations(text, [], {'sources':[SOURCE]}, complete)
        # The old undefined URL1 in the internal table must not be rebound.
        self.assertIn('- [URL2] https://example.com/gtf', repaired)
        self.assertNotIn('- [URL1]', repaired)
        self.assertTrue(audit['after']['passed'])

    async def test_unsupported_paragraph_does_not_get_a_token_citation(self):
        async def complete(prompt):
            packet = json.loads(prompt.split('材料：\n')[1])
            return json.dumps({'bindings':[{'id':packet['paragraphs'][0]['id'], 'supports_paragraph':False,
                                           'evidence':[], 'reason':'范围和时间不符'}]})
        repaired, audit = await repair_body_citations(report(), [], {'sources':[SOURCE]}, complete)
        self.assertEqual(repaired, report())
        self.assertFalse(audit['after']['passed'])
