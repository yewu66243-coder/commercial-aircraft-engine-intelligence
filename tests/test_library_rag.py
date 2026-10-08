import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gpt_researcher.document.library_rag import (
    build_library_index, search_library, inventory, connect, database_path,
    extend_selection_with_matches, build_lock,
)
from gpt_researcher.document.local_index import LocalIndexSelection


class Embeddings:
    model = "test-library"

    def embed_query(self, text):
        return [float("engine" in text), float("oxygen" in text), 0.01]

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]


class LibraryRagTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.roots = {scope: self.root / scope for scope in ("papers", "user_docs", "patents")}
        for root in self.roots.values():
            root.mkdir()
        self.paper = self.roots["papers"] / "generic.txt"
        self.paper.write_text("engine maintenance findings", encoding="utf-8")
        nested = self.roots["user_docs"] / "nested"
        nested.mkdir()
        self.user = nested / "oxygen.txt"
        self.user.write_text("oxygen safety", encoding="utf-8")
        env = patch.dict("os.environ", {"LOCAL_RAG_INDEX_DIR": str(self.root / "index")})
        env.start()
        self.addCleanup(env.stop)
        self.embeddings = Embeddings()

    def build(self, **kwargs):
        return build_library_index(roots=self.roots, embeddings=self.embeddings, **kwargs)

    def search(self, query="engine", scopes=("papers", "user_docs")):
        return search_library(query, scopes=scopes, embeddings=self.embeddings)

    def test_inventory_is_recursive_and_incremental(self):
        self.assertEqual(len(inventory(self.roots)), 2)
        self.assertEqual(self.build()["indexed"], 2)
        with patch.object(self.embeddings, "embed_documents", side_effect=AssertionError("Must reuse")):
            self.assertEqual(self.build()["reused"], 2)

    def test_duplicate_builder_is_rejected(self):
        with build_lock(self.root / "index"):
            with self.assertRaises(RuntimeError):
                self.build()

    def test_pdf_page_locator(self):
        import fitz
        path = self.roots["papers"] / "pages.pdf"
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), "oxygen safety")
            document.new_page().insert_text((72, 72), "engine maintenance")
            document.save(path)
        self.build()
        matches = self.search(scopes=["papers"])["matches"]
        self.assertTrue(any(m.chunk.file_name == "pages.pdf" and m.chunk.page == 2 for m in matches))

    def test_body_search_and_scope_isolation(self):
        self.build()
        self.assertEqual(self.search()["matches"][0].chunk.file_name, "generic.txt")
        matches = self.search(scopes=["user_docs"])["matches"]
        self.assertTrue(matches)
        self.assertTrue(all(m.chunk.source_type == "用户资料" for m in matches))
        self.assertFalse(self.search(scopes=["web"])["matches"])

    def test_changed_and_deleted_files(self):
        self.build()
        self.paper.write_text("oxygen certification update", encoding="utf-8")
        self.assertFalse(self.search(scopes=["papers"])["matches"])
        self.assertEqual(self.build()["indexed"], 1)
        self.paper.unlink()
        self.assertEqual(self.build()["document_status_counts"], {"indexed": 1})

    def test_model_change_does_not_search_old_vectors(self):
        self.build()
        self.embeddings.model = "changed-model"
        self.assertFalse(self.search()["matches"])
        self.assertEqual(self.build()["indexed"], 2)

    def test_failed_service_and_resume(self):
        with patch.object(self.embeddings, "embed_documents", side_effect=ConnectionError("offline")):
            result = self.build()
        self.assertEqual(result["status"], "interrupted")
        self.assertFalse(self.search()["matches"])
        self.assertEqual(self.build()["indexed"], 2)

    def test_unsupported_and_empty_are_not_indexed(self):
        (self.roots["papers"] / "old.doc").write_bytes(b"unsupported")
        (self.roots["papers"] / "empty.txt").write_text("")
        result = self.build()
        self.assertEqual(result["unsupported"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["indexed"], 2)

    def test_stop_resumes_completed_documents(self):
        def progress(state):
            if state["indexed"] == 1:
                (self.root / "index" / "library-build.stop").write_text("stop")
        result = self.build(progress=progress)
        self.assertEqual(result["status"], "paused")
        result = self.build()
        self.assertEqual(result["reused"], 1)
        self.assertEqual(result["indexed"], 1)

    def test_selection_gets_body_only_match(self):
        self.build()
        selection = LocalIndexSelection("engine", "", "", str(self.root / "selected"), [], [])
        matches = self.search(scopes=["papers"])["matches"]
        self.assertEqual(extend_selection_with_matches(selection, matches), 1)
        self.assertTrue(selection.has_documents)
        self.assertTrue((Path(selection.doc_path) / "generic.txt").exists())
        self.assertTrue((Path(selection.doc_path) / "selected_sources.json").exists())

    def test_xlsx_text_is_indexed(self):
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.active.append(["engine", "maintenance interval", 100])
        workbook.save(self.roots["user_docs"] / "records.xlsx")
        workbook.close()
        self.assertEqual(self.build()["indexed"], 3)


if __name__ == "__main__":
    unittest.main()
