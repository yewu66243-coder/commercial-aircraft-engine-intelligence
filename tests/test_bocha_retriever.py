import unittest
from unittest.mock import Mock, patch

import requests

from gpt_researcher.actions.retriever import get_retriever
from gpt_researcher.retrievers.bocha.bocha import BoChaSearch
from gpt_researcher.retrievers.search_diagnostics import search_trace


class BoChaRetrieverTests(unittest.TestCase):
    def setUp(self):
        self.trace = []
        self.token = search_trace.set(self.trace)

    def tearDown(self):
        search_trace.reset(self.token)

    def test_bocha_is_registered(self):
        self.assertIs(get_retriever("bocha"), BoChaSearch)

    @patch.dict("os.environ", {"BOCHA_API_KEY": "test-key"}, clear=False)
    @patch("gpt_researcher.retrievers.bocha.bocha.requests.post")
    def test_search_normalizes_bocha_response(self, post):
        response = Mock()
        response.json.return_value = {
            "data": {
                "webPages": {
                    "value": [
                        {
                            "name": "RTX update",
                            "url": "https://example.com/news",
                            "snippet": "Powder metal inspection update",
                        }
                    ]
                }
            }
        }
        response.raise_for_status.return_value = None
        post.return_value = response

        results = BoChaSearch("GTF powder metal", query_domains=["rtx.com"]).search(max_results=3)

        self.assertEqual(results[0]["title"], "RTX update")
        self.assertEqual(results[0]["href"], "https://example.com/news")
        self.assertIn("Powder metal", results[0]["body"])
        payload = post.call_args.kwargs["json"]
        self.assertIn("site:rtx.com", payload["query"])
        self.assertEqual(payload["count"], 3)
        self.assertEqual(self.trace[-1]["provider"], "bocha")
        self.assertEqual(self.trace[-1]["status"], "ok")

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_key_returns_empty_and_records_reason(self):
        self.assertEqual(BoChaSearch("GTF").search(), [])
        self.assertEqual(self.trace[-1]["status"], "failed")
        self.assertIn("密钥", self.trace[-1]["message"])

    @patch.dict("os.environ", {"BOCHA_API_KEY": "test-key"}, clear=False)
    @patch("gpt_researcher.retrievers.bocha.bocha.requests.post")
    def test_http_error_records_safe_diagnosis(self, post):
        response = requests.Response()
        response.status_code = 429
        error = requests.HTTPError("contains-test-key", response=response)
        post.side_effect = error

        self.assertEqual(BoChaSearch("GTF").search(), [])
        self.assertEqual(self.trace[-1]["http_status"], 429)
        self.assertIn("频率或额度", self.trace[-1]["message"])
        self.assertNotIn("test-key", str(self.trace))


if __name__ == "__main__":
    unittest.main()
