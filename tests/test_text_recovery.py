import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from gpt_researcher.document.text_recovery import (
    CACHE_VERSION, cache_path, read_recovered_text, recover_source, order_ocr_lines,
)
from gpt_researcher.document.local_rag import chunk_source
from gpt_researcher.document.library_rag import build_library_index
from gpt_researcher.document.document import DocumentLoader
from backend.reporting.source_grounding import build_source_catalog


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict('os.environ', {'LOCAL_RAG_INDEX_DIR':str(self.root / 'index')})
        env.start()
        self.addCleanup(env.stop)
        self.pool = self.root / 'papers'
        self.pool.mkdir()
        self.path = self.pool / 'scan.pdf'
        with fitz.open() as document:
            document.new_page()
            document.new_page()
            document.save(self.path)

    def cache(self):
        target = cache_path(self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({'version':CACHE_VERSION, 'complete':True,
                                     'method':'ocr', 'pages':[{'page':2, 'text':'engine maintenance'}]}), encoding='utf-8')

    def test_content_hash_invalidates_cache(self):
        self.cache()
        self.assertTrue(read_recovered_text(self.path))
        self.path.write_bytes(b'changed')
        self.assertIsNone(read_recovered_text(self.path))

    def test_copied_sources_reuse_cache(self):
        self.cache()
        copied = self.root / 'renamed.pdf'
        copied.write_bytes(self.path.read_bytes())
        self.assertEqual(read_recovered_text(copied)['pages'][0]['page'], 2)

    def test_chunks_and_catalog_preserve_ocr_page(self):
        self.cache()
        source = {'source_path':str(self.path), 'file_name':'scan.pdf'}
        chunks = chunk_source(source, chunk_size=100, overlap=10)
        self.assertEqual(chunks[0]['page'], 2)
        self.assertEqual(chunks[0]['extraction_method'], 'ocr')
        catalog = build_source_catalog([source], [])
        self.assertEqual(catalog['readable_sources'], 1)
        self.assertEqual(catalog['sources'][0]['pages'][0]['extraction_method'], 'ocr')
        docs = asyncio.run(DocumentLoader(str(self.path))._load_document(str(self.path), 'pdf'))
        self.assertEqual(docs[0].metadata['page'], 2)

    def test_three_columns_are_not_interleaved(self):
        lines = []
        for row in range(12):
            for column in range(3):
                x, y = 40 + column * 310, 250 + row * 40
                lines.append(([[x,y],[x+270,y],[x+270,y+20],[x,y+20]], f'{column}-{row}', .99))
        ordered = order_ocr_lines(lines, 1000, 1000)
        self.assertEqual([line[1] for line in ordered[:12]], [f'0-{r}' for r in range(12)])

    def test_ocr_resume_keeps_original_page_numbers(self):
        calls = []
        def engine(pixels):
            calls.append(1)
            return [([[0,0],[10,0],[10,10],[0,10]], 'engine text', .99)], []
        with patch('gpt_researcher.document.text_recovery._ENGINE', engine):
            with self.assertRaises(InterruptedError):
                recover_source(self.path, stopped=lambda: bool(calls))
            self.assertIsNone(read_recovered_text(self.path))
            result = recover_source(self.path)
        self.assertEqual(len(calls), 2)
        self.assertEqual([p['page'] for p in result['pages']], [1,2])

    def test_repair_only_preserves_completed_sources(self):
        class Embeddings:
            model = 'recovery-test'
            def embed_documents(self, texts):
                return [[1., .5] for _ in texts]
        (self.pool / 'good.txt').write_text('engine text')
        initial = build_library_index(roots={'papers':self.pool}, embeddings=Embeddings())
        self.assertEqual(initial['failed'], 1)
        self.cache()
        repaired = build_library_index(roots={'papers':self.pool}, embeddings=Embeddings(), repair=True)
        self.assertEqual(repaired['run_files'], 1)
        self.assertEqual(repaired['document_status_counts'], {'indexed':2})


if __name__ == '__main__':
    unittest.main()
