import unittest
from unittest.mock import patch
import requests

from gpt_researcher.retrievers.tavily.tavily_search import TavilySearch
from gpt_researcher.retrievers.search_diagnostics import search_trace


class SearchFallbackTests(unittest.TestCase):
    def setUp(self):
        TavilySearch._unavailable.clear()
        self.trace = []
        self.token = search_trace.set(self.trace)

    def tearDown(self):
        search_trace.reset(self.token)
        TavilySearch._unavailable.clear()

    def test_quota_failure_is_distinguished_and_falls_back_without_leaking_key(self):
        response = requests.Response()
        response.status_code = 432
        error = requests.HTTPError('SECRET-KEY', response=response)
        with patch.dict('os.environ', {'TAVILY_API_KEY': 'SECRET-KEY'}), \
             patch.object(TavilySearch, '_search', side_effect=error) as primary, \
             patch.object(TavilySearch, '_fallback', return_value=[{'href': 'https://example.com'}]):
            self.assertTrue(TavilySearch('GTF').search())
            self.assertTrue(TavilySearch('GTF powder metal').search())
        self.assertEqual(primary.call_count, 1)
        self.assertEqual(self.trace[0]['http_status'], 432)
        self.assertEqual(self.trace[1]['status'], 'cooldown')
        self.assertNotIn('SECRET-KEY', str(self.trace))

    def test_success_does_not_fallback(self):
        with patch.object(TavilySearch, '_search', return_value={'results': [{'url': 'https://example.com', 'content': 'body'}]}), \
             patch.object(TavilySearch, '_fallback') as fallback:
            self.assertEqual(TavilySearch('GTF').search()[0]['href'], 'https://example.com')
        fallback.assert_not_called()
        self.assertEqual(self.trace[0]['status'], 'ok')

    def test_empty_results_have_different_diagnosis_from_http_error(self):
        with patch.object(TavilySearch, '_search', return_value={'results': []}), \
             patch.object(TavilySearch, '_fallback', return_value=[]):
            TavilySearch('GTF').search()
        self.assertEqual(self.trace[0]['status'], 'empty')
        self.assertIsNone(self.trace[0]['http_status'])
