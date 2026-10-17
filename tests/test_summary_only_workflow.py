"""The workbench has no configurable full-text retrieval or upload queue."""
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import UploadFile


ROOT = Path(__file__).resolve().parents[1]


class SummaryOnlyWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_updates_summary_without_queue_fields(self):
        import main

        for target in ("user_docs", "all_papers_pool", "all_patent_pool"):
            for duplicate in (False, True):
                with self.subTest(target=target, duplicate=duplicate):
                    result = {"file_name": "engine.txt", "path": "unused/engine.txt",
                              "duplicate": duplicate, "index_method": "file_preview"}
                    library = {"user_docs": [], "papers": [], "patents": []}
                    upload = UploadFile(filename="engine.txt", file=io.BytesIO(b"engine"))
                    try:
                        with patch.dict("os.environ", {
                            "REPORT_LOCAL_RAG_ENABLED": "true",
                            "UPLOAD_FULLTEXT_INDEX_ENABLED": "true",
                        }), patch.object(main, "save_local_library_file", return_value=result) as save, \
                             patch.object(main, "list_local_library", return_value=library), \
                             patch("gpt_researcher.document.upload_indexing.enqueue") as enqueue, \
                             patch("gpt_researcher.document.upload_indexing.ensure_worker") as worker:
                            response = await main.upload_local_library_file(target=target, file=upload)
                            save.assert_called_once_with(upload.file, "engine.txt", target)
                            enqueue.assert_not_called()
                            worker.assert_not_called()
                        self.assertEqual(response, {"success": True, **result, "library": library})
                    finally:
                        await upload.close()

    async def test_queue_routes_are_not_registered(self):
        import main
        import httpx

        self.assertFalse(any("/local-library/index-jobs" in path
                             for path in main.app.openapi()["paths"]))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://test", follow_redirects=True) as client:
            response = await client.get("/api/local-library/index-jobs")
            # The generic file-delete route may match the path but reject GET.
            self.assertIn(response.status_code, (404, 405))
            response = await client.post("/api/local-library/index-jobs/old-job/retry")
            self.assertIn(response.status_code, (404, 405))
        self.assertFalse(hasattr(main, "upload_fulltext_index_enabled"))

    async def test_configuration_has_no_fulltext_switches(self):
        for name in (".env", ".env.example"):
            path = ROOT / name
            if path.is_file():
                text = path.read_text(encoding="utf-8-sig")
                self.assertNotIn("REPORT_LOCAL_RAG_ENABLED", text)
                self.assertNotIn("UPLOAD_FULLTEXT_INDEX_ENABLED", text)

    async def test_frontend_has_no_queue_controls_or_polling(self):
        script = (ROOT / "frontend/scripts.js").read_text(encoding="utf-8")
        page = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        self.assertNotIn("/api/local-library/index-jobs", script)
        self.assertNotIn("fulltext_indexing_enabled", script)
        self.assertNotIn("uploadIndexJobs", page)
        self.assertIn("资料已上传，已更新摘要索引", script)


if __name__ == "__main__":
    unittest.main()
