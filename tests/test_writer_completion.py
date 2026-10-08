import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from backend.reporting.writer_completion import complete_writer


def response(text, reason='stop'):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=reason)],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150))


class WriterCompletionTests(unittest.TestCase):
    def run_writer(self, replies, **kwargs):
        create = AsyncMock(side_effect=replies)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = asyncio.run(complete_writer(client, messages=[{'role': 'user', 'content': '证据与写作要求'}],
                                             model='test', max_tokens=8192, **kwargs))
        return result, create

    def test_truncated_word_continues_without_corrupting_model_name(self):
        result, create = self.run_writer([response('是否扩展至PW1500', 'length'), response('G？\n剩余内容。')])
        self.assertEqual(result['content'], '是否扩展至PW1500G？\n剩余内容。')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['warning'], '')
        self.assertEqual(result['attempts'][0]['finish_reason'], 'length')
        self.assertEqual(result['attempts'][1]['usage']['completion_tokens'], 50)
        self.assertEqual(create.call_args_list[1].kwargs['messages'][0]['content'], '证据与写作要求')
        self.assertEqual(create.call_args_list[1].kwargs['messages'][1]['content'], '是否扩展至PW1500')

    def test_normal_completion_needs_no_continuation(self):
        result, create = self.run_writer([response('完整正文。')])
        self.assertEqual(create.await_count, 1)
        self.assertEqual(result['status'], 'completed')

    def test_continuation_limit_preserves_all_partial_text(self):
        result, create = self.run_writer([response(t, 'length') for t in ('第一段', '第二段', '第三段')])
        self.assertEqual(create.await_count, 3)
        self.assertEqual(result['status'], 'truncated')
        self.assertEqual(result['content'], '第一段第二段第三段')

    def test_failed_continuation_preserves_prefix_without_logging_secrets(self):
        result, _ = self.run_writer([response('已有正文', 'length'), RuntimeError('private-api-key')])
        self.assertEqual(result['content'], '已有正文')
        self.assertEqual(result['status'], 'request_failed')
        self.assertNotIn('private-api-key', str(result))

    def test_initial_request_failure_and_empty_response_are_distinct(self):
        for reply, status in ((RuntimeError('failure'), 'request_failed'), (response(''), 'empty_response')):
            result, _ = self.run_writer([reply])
            self.assertEqual(result['status'], status)

    def test_non_length_interruption_is_not_automatically_continued(self):
        result, create = self.run_writer([response('正文', 'content_filter')])
        self.assertEqual(create.await_count, 1)
        self.assertEqual(result['status'], 'interrupted')

    def test_restarted_report_is_rejected(self):
        result, _ = self.run_writer([response('# 报告\n已有内容', 'length'), response('# 报告\n重新写')])
        self.assertEqual(result['status'], 'invalid_continuation')
        self.assertEqual(result['content'], '# 报告\n已有内容')

    def test_exact_overlap_is_not_duplicated(self):
        paragraph = '原文明确说明这一发动机型号需要进一步的定期检查和维修。'
        result, _ = self.run_writer([response('前文。' + paragraph, 'length'), response(paragraph + '后文。')])
        self.assertEqual(result['content'], '前文。' + paragraph + '后文。')

    def test_writer_service_recovers_or_keeps_specific_draft_reason(self):
        from three_agent_service import ThreeAgentService, ThreeAgentRequestData
        for final, expected in ((response('G。'), 'ready'), (RuntimeError('network'), 'draft')):
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                create=AsyncMock(side_effect=[response('PW1500', 'length'), final]))))
            with patch.dict('os.environ', {'OPENAI_API_KEY': 'test'}, clear=True), \
                 patch('three_agent_service.AsyncOpenAI', return_value=client), \
                 patch('three_agent_service.review_content', return_value={'needs_enrichment': False}):
                service = ThreeAgentService(ThreeAgentRequestData(task='GTF'))
                text = asyncio.run(service.writer_agent([]))
                self.assertEqual(service.generation_status, expected)
                self.assertTrue(text.startswith('PW1500'))
                diagnostics = service.content_enrichment['writer_completion']
                self.assertEqual(len(diagnostics['attempts']), 2)
                if expected == 'draft':
                    asyncio.run(service.editorial_agent(text))
                    self.assertIn('续写请求失败', service.editorial_review['reason'])
                else:
                    self.assertEqual(text, 'PW1500G。')

    def test_request_timeout_is_bounded(self):
        async def stalled(**kwargs):
            await asyncio.sleep(1)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=stalled)))
        result = asyncio.run(complete_writer(client, messages=[], model='test', max_tokens=10,
                                             timeout_seconds=.01))
        self.assertEqual(result['status'], 'request_failed')
        self.assertEqual(result['attempts'][0]['error_type'], 'TimeoutError')
