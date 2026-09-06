import asyncio
import importlib
import os
import sys
import unittest
from unittest.mock import patch, AsyncMock
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote

sys.path.insert(0, os.getcwd())


class FormalPipelineTests(unittest.TestCase):
    def test_offline_pipeline_writes_real_word_pdf_and_markdown(self):
        from three_agent_service import ThreeAgentService, ThreeAgentRequestData
        from test_formal_report import BASE
        from docx import Document
        from pypdf import PdfReader
        service = ThreeAgentService(ThreeAgentRequestData(task='GTF离线验收', report_source='web'))
        service.generation_status = 'ready'
        original_cwd = os.getcwd()
        with TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch.object(service, 'pre_search_abstracts', new=AsyncMock()))
            stack.enter_context(patch.object(service, 'planner_agent', return_value=['GTF']))
            stack.enter_context(patch.object(service, 'research_agent', new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(service, 'collect_report_images'))
            stack.enter_context(patch.object(service, 'writer_agent', new=AsyncMock(return_value=BASE)))
            stack.enter_context(patch.object(service, 'inspect_report_urls', new=AsyncMock(return_value={})))
            stack.enter_context(patch.object(service, 'append_evaluation_record', return_value='offline-record'))
            stack.enter_context(patch('three_agent_service.evaluate_public_url_sources', return_value={}))
            stack.enter_context(patch('three_agent_service.prune_redundant_unchecked_url_citations', side_effect=lambda text, stats: (text, {})))
            stack.enter_context(patch('three_agent_service.evaluate_report_entities', return_value={}))
            try:
                os.chdir(temporary)
                result = asyncio.run(service.run())
                self.assertTrue(all(result['export_status'].values()))
                md = Path(unquote(result['md_path'])).read_text(encoding='utf-8')
                self.assertEqual(md, result['report'])
                word = Document(unquote(result['word_path']))
                pdf = PdfReader(unquote(result['pdf_path']))
                self.assertIn('35天', '\n'.join(p.text for p in word.paragraphs))
                self.assertIn('35天', '\n'.join(p.extract_text() for p in pdf.pages))
                self.assertIn('ref-1', pdf.named_destinations)
            finally:
                os.chdir(original_cwd)

    def test_pipeline_evaluates_original_ids_and_publishes_only_numbered_report(self):
        from three_agent_service import ThreeAgentService, ThreeAgentRequestData
        from test_formal_report import BASE
        service = ThreeAgentService(ThreeAgentRequestData(task='GTF研究', report_source='web'))
        service.generation_status = 'ready'
        url_check = {'accessibility_rate': 1, 'total_urls': 1, 'checked_urls': 1,
                     'accessible_urls': 1, 'failed_urls': 0, 'results': []}
        saved = []
        annotated = BASE.replace('35天[URL8]', '35天[URL8]（该点待后续核验）', 1)
        with ExitStack() as stack:
            stack.enter_context(patch.object(service, 'pre_search_abstracts', new=AsyncMock()))
            stack.enter_context(patch.object(service, 'planner_agent', return_value=['GTF']))
            stack.enter_context(patch.object(service, 'research_agent', new=AsyncMock(return_value=[])))
            stack.enter_context(patch.object(service, 'collect_report_images'))
            stack.enter_context(patch.object(service, 'writer_agent', new=AsyncMock(return_value=annotated)))
            stack.enter_context(patch.object(service, 'inspect_report_urls', new=AsyncMock(return_value=url_check)))
            stack.enter_context(patch.object(service, 'append_evaluation_record', side_effect=lambda record: saved.append(record) or 'record.json'))
            source_check = stack.enter_context(patch('three_agent_service.evaluate_public_url_sources', return_value={'supported_count': 1}))
            stack.enter_context(patch('three_agent_service.prune_redundant_unchecked_url_citations', side_effect=lambda text, stats: (text, {'changed': False})))
            stack.enter_context(patch('three_agent_service.evaluate_report_entities', return_value={'extracted_count': 1, 'auto_evidence_eval': {'unchecked_count': 1}}))
            md = stack.enter_context(patch('three_agent_service.write_text_to_md', new=AsyncMock(return_value='outputs/report.md')))
            word = stack.enter_context(patch('three_agent_service.write_md_to_word', new=AsyncMock(return_value='outputs/report.docx')))
            stack.enter_context(patch('three_agent_service.write_md_to_pdf', new=AsyncMock(return_value='')))
            result = asyncio.run(service.run())
        self.assertIn('[URL8]', source_check.call_args.args[0])
        self.assertNotIn('[URL8]', result['report'])
        self.assertNotIn('运行统计', result['report'])
        self.assertEqual(md.call_args.args[0], word.call_args.args[0])
        self.assertEqual(result['report'], md.call_args.args[0])
        self.assertEqual(result['export_status']['pdf'], False)
        self.assertIn('部分实体或参数的来源支撑需复核。', result['report_quality']['warnings'])
        self.assertEqual(saved[0]['status'], 'export_partial')
        self.assertTrue(saved[0]['citation_map'])
        self.assertIn('[URL8]', saved[0]['evidence_report'])
        self.assertNotIn('该点待后续核验', result['report'])
        self.assertIn('该点待后续核验', saved[0]['evidence_report'])
        self.assertEqual(len(saved[0]['verification_notes']), 1)
        self.assertFalse(any('文件全部生成完毕' in item['message'] for item in result['trace']))

    def test_writer_failure_is_explicit_draft_in_service(self):
        from three_agent_service import ThreeAgentService, ThreeAgentRequestData
        service = ThreeAgentService(ThreeAgentRequestData(task='GTF研究'))
        with patch('three_agent_service.AsyncOpenAI', None):
            report = asyncio.run(service.writer_agent([{'title': '资料', 'subtopic': 'GTF', 'draft': '原始研究材料'}]))
        self.assertEqual(getattr(service, 'generation_status', None), 'draft')
        self.assertIn('草稿', report)
        self.assertNotIn('Demo', report)

    def test_report_type_is_accepted_from_frontend(self):
        from three_agent_service import ThreeAgentRequestData
        self.assertIn('report_type', ThreeAgentRequestData.__dataclass_fields__)

    def test_writer_contract_matches_academic_structure(self):
        try:
            module = importlib.import_module('backend.reporting.prompts')
        except ModuleNotFoundError:
            module = None
        self.assertIsNotNone(module, 'Formal writing contract is missing')
        prompt = module.build_writer_prompt(task='GTF', tone='objective', report_type='detailed_report', sources_text='', demand_text='', source_template_text='', image_text='', sections_text='', method_context='检索记录时间2026-09-04')
        for required in ('摘要', '关键词', '资料来源与研究方法', '综合讨论与研究局限', '结论与建议', '核心实体与参数清单（内部核验）'):
            self.assertIn(required, prompt)
        self.assertIn('检索记录时间2026-09-04', prompt)
        self.assertNotIn('严格为以下五部分', prompt)


if __name__ == '__main__':
    unittest.main()
