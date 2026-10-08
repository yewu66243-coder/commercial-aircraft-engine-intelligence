import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from gpt_researcher.document.local_index import SelectedLocalPaper
from gpt_researcher.document.local_rag import (
    _chunk_text, build_or_update_local_rag_index, load_rag_chunks,
    retrieve_local_rag_matches, _embed_query,
)


class FakeEmbeddings:
    model = "test-v1"

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        return [float(term in text) for term in ("oxygen", "engine", "maintenance")]


class FlakyEmbeddings:
    def __init__(self):
        self.calls = 0

    def embed_query(self, text):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Ollama embedding failed: status code: 502")
        return [1.0, 0.0, 0.0]


class LocalRagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict("os.environ", {"LOCAL_RAG_INDEX_DIR": str(self.root / "index")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.embed = FakeEmbeddings()
        self.source = self.make_source("oxygen.txt", "oxygen system certification. " * 30)

    def make_source(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return SelectedLocalPaper("", name, str(path), 1)

    def build(self, sources=None, **kwargs):
        return build_or_update_local_rag_index(
            sources or [self.source], local_docs_root=self.root, embeddings=self.embed,
            chunk_size=100, overlap=20, **kwargs,
        )

    def test_retrieve_and_scope_filter(self):
        other = self.make_source("engine.txt", "engine maintenance manual")
        self.build([self.source, other])
        result = retrieve_local_rag_matches("oxygen", sources=[self.source],
                                            embeddings=self.embed, local_docs_root=self.root, max_chunks=2)
        self.assertEqual(len(result["matches"]), 2)
        self.assertIn("oxygen.txt", result["context"])
        self.assertNotIn("engine.txt", result["context"])
        self.assertEqual(result["matches"][0].chunk.title, "oxygen")

    def test_incremental_and_changed_file(self):
        self.build()
        self.assertFalse(self.build()["updated"])
        Path(self.source.source_path).write_text("engine maintenance", encoding="utf-8")
        self.assertTrue(self.build()["updated"])
        self.assertEqual([c.text for c in load_rag_chunks(self.root)], ["engine maintenance"])

    def test_model_change_rebuilds(self):
        self.build()
        self.embed.model = "test-v2"
        self.assertTrue(self.build()["updated"])

    def test_query_rejects_stale_model_even_with_same_dimensions(self):
        self.build()
        self.embed.model = "different-model"
        with self.assertRaises(ValueError):
            retrieve_local_rag_matches("oxygen", embeddings=self.embed, local_docs_root=self.root)

    def test_deleted_source_removed(self):
        self.build()
        Path(self.source.source_path).unlink()
        self.build()
        self.assertEqual(load_rag_chunks(self.root), [])

    def test_embedding_failure_preserves_index(self):
        self.build()
        original = (self.root / "index" / "chunks.jsonl").read_bytes()
        Path(self.source.source_path).write_text("engine maintenance", encoding="utf-8")
        with patch.object(self.embed, "embed_documents", return_value=[]):
            with self.assertRaises(ValueError):
                self.build()
        self.assertEqual(original, (self.root / "index" / "chunks.jsonl").read_bytes())

    def test_chunk_boundaries_do_not_skip_text(self):
        text = "a" * 60 + "." + "b" * 25 + "c" * 80
        chunks = _chunk_text(text, chunk_size=100, overlap=10)
        self.assertTrue(any("b" * 25 in chunk for chunk in chunks))

    def test_zero_overlap_and_invalid_size(self):
        result = build_or_update_local_rag_index([self.source], local_docs_root=self.root,
                                                embeddings=self.embed, chunk_size=100, overlap=0)
        self.assertGreater(result["added_chunks"], 1)
        with self.assertRaises(ValueError):
            build_or_update_local_rag_index([self.source], chunk_size=100, overlap=100)

    def test_embedding_502_is_retried(self):
        embeddings = FlakyEmbeddings()
        with patch.dict("os.environ", {
            "LOCAL_RAG_INDEX_DIR": str(self.root / "index"),
            "LOCAL_RAG_EMBED_RETRIES": "2",
            "LOCAL_RAG_EMBED_RETRY_BASE_SECONDS": "0",
            "LOCAL_RAG_EMBED_RETRY_MAX_SECONDS": "0",
        }):
            vector = _embed_query(embeddings, "engine")
        self.assertEqual(vector, [1.0, 0.0, 0.0])
        self.assertEqual(embeddings.calls, 2)


class RagServiceTests(unittest.IsolatedAsyncioTestCase):
    async def run_selection(self, fail=False):
        from three_agent_service import ThreeAgentService
        from unittest.mock import Mock

        service = ThreeAgentService.__new__(ThreeAgentService)
        service.request = SimpleNamespace(max_search_results=5)
        service.expanded_task_query = "engine"
        service.selected_search_scopes = lambda: {"papers"}
        service.demand_profile_summary = lambda: "test"
        service._log = Mock()
        service.rag_retrieval_context = ""
        service.rag_retrieval_stats = {}
        source = SelectedLocalPaper("engine", "engine.txt", "engine.txt", 1)
        selection = SimpleNamespace(has_documents=True, selected=[source], doc_path="selected")
        stats = {"errors": [], "indexed_chunks": 1}
        with patch.dict("os.environ", {"LOCAL_RAG_ENABLED": "true"}), \
             patch("three_agent_service.search_library", return_value={"matches": [], "chunk_count": 0}), \
             patch("three_agent_service.prepare_local_docs_for_query", return_value=selection), \
             patch("three_agent_service.build_or_update_local_rag_index", return_value=stats,
                   side_effect=RuntimeError("offline") if fail else None), \
             patch("three_agent_service.retrieve_local_rag_matches",
                   return_value={"context": "source excerpt", "matches": []}), \
             patch("three_agent_service.logging.getLogger"):
            await service.pre_search_abstracts()
        return service

    async def test_pipeline_receives_context(self):
        service = await self.run_selection()
        self.assertEqual(service.rag_retrieval_context, "source excerpt")
        self.assertTrue(service.rag_retrieval_stats["enabled"])

    async def test_pipeline_falls_back_to_selected_documents(self):
        service = await self.run_selection(fail=True)
        self.assertFalse(service.rag_retrieval_stats["enabled"])
        self.assertEqual(service.local_doc_path, "selected")
        self.assertEqual(len(service.selected_local_papers), 1)
        self.assertEqual(service.rag_retrieval_context, "")


if __name__ == "__main__":
    unittest.main()
