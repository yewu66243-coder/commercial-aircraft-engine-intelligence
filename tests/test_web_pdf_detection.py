import unittest
from types import SimpleNamespace
from unittest.mock import patch
import fitz

from gpt_researcher.scraper.beautiful_soup.beautiful_soup import BeautifulSoupScraper
from gpt_researcher.evaluation.entity_evaluator import _read_url_text


class WebPDFTests(unittest.TestCase):
    def test_pdf_without_extension_or_correct_content_type(self):
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((50, 50), 'GTF powder metal: 600 to 700 engines')
            data = document.tobytes()
        class Response:
            def __init__(self):
                import io
                from email.message import Message
                self.stream = io.BytesIO(data)
                self.headers = Message()
                self.headers['Content-Type'] = 'application/octet-stream'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, size):
                return self.stream.read(size)
        with patch('gpt_researcher.evaluation.entity_evaluator.urlopen', side_effect=lambda *a, **k: Response()):
            text = _read_url_text('https://example.com/static-files/id')
        self.assertIn('600 to 700', text)
        response = SimpleNamespace(content=data, raise_for_status=lambda: None)
        session = SimpleNamespace(get=lambda *a, **k: response)
        text, _, _ = BeautifulSoupScraper('https://example.com/static-files/id', session).scrape()
        self.assertIn('powder metal', text)
