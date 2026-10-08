import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gpt_researcher.document.library_rag import build_library_index, build_lock, connect, database_path
from gpt_researcher.document.upload_indexing import (
    _index_failure_message,
    enqueue,
    jobs_db,
    list_jobs,
    process_job,
    retry,
)


class Embeddings:
    model = "test-upload"

    def embed_query(self, text):
        return [float("engine" in text), float("manual" in text), 0.01]

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]


class UploadIndexingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local_docs = self.root / "local_docs"
        self.user_docs = self.local_docs / "user_docs"
        self.papers = self.local_docs / "all_papers_pool"
        self.patents = self.local_docs / "all_patent_pool"
        for directory in (self.user_docs, self.papers, self.patents):
            directory.mkdir(parents=True)
        self.env = patch.dict(
            "os.environ",
            {
                "LOCAL_DOCS_PATH": str(self.local_docs),
                "LOCAL_RAG_INDEX_DIR": str(self.root / "rag_index"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.embeddings = Embeddings()

    def build(self, **kwargs):
        return build_library_index(
            roots={
                "user_docs": self.user_docs,
                "papers": self.papers,
                "patents": self.patents,
            },
            embeddings=self.embeddings,
            **kwargs,
        )

    def latest_job(self):
        with jobs_db() as db:
            return db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 1").fetchone()

    def test_enqueue_dedupes_active_job_and_retry_reuses_failed_job(self):
        source = self.user_docs / "manual.txt"
        source.write_text("engine manual", encoding="utf-8")
        first = enqueue(source, "user_docs")
        second = enqueue(source, "user_docs")
        self.assertEqual(first["id"], second["id"])
        with jobs_db() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (first["id"],))
        retried = retry(first["id"])
        self.assertEqual(first["id"], retried["id"])
        self.assertEqual(retried["status"], "queued")

    def test_process_job_indexes_file_and_refreshes_summary(self):
        source = self.user_docs / "engine_manual.txt"
        source.write_text("engine manual findings for maintenance", encoding="utf-8")
        enqueue(source, "user_docs")
        process_job(self.latest_job(), build=self.build)
        jobs = list_jobs()
        self.assertEqual(jobs[0]["status"], "ready")
        with contextlib.closing(sqlite3.connect(database_path())) as db:
            status = db.execute("SELECT status FROM documents WHERE path=?", (str(source.resolve()),)).fetchone()
        self.assertEqual(status[0], "indexed")
        index_text = (self.local_docs / "user_docs_index.json").read_text(encoding="utf-8")
        self.assertIn("engine_manual.txt", index_text)

    def test_busy_full_library_lock_marks_job_waiting(self):
        source = self.user_docs / "waiting.txt"
        source.write_text("engine manual", encoding="utf-8")
        enqueue(source, "user_docs")
        with build_lock(self.root / "rag_index"):
            process_job(self.latest_job(), build=self.build)
        self.assertEqual(list_jobs()[0]["status"], "waiting")

    def test_no_text_file_is_failed_with_user_facing_message(self):
        source = self.user_docs / "empty.txt"
        source.write_text("", encoding="utf-8")
        enqueue(source, "user_docs")
        process_job(self.latest_job(), build=self.build)
        job = list_jobs()[0]
        self.assertEqual(job["status"], "failed")
        self.assertIn("未提取到可用正文", job["message"])

    def test_existing_failed_documents_do_not_fail_successful_upload(self):
        source = self.user_docs / "ok.txt"
        source.write_text("engine manual", encoding="utf-8")
        database_path().parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(connect(database_path())) as db:
            db.execute(
                "INSERT INTO documents(path,scope,status,error,updated_at) VALUES (?,?,?,?,?)",
                (str((self.user_docs / "old-empty.txt").resolve()), "user_docs", "no_text", "old", "now"),
            )
            db.commit()
        enqueue(source, "user_docs")
        process_job(self.latest_job(), build=self.build)
        self.assertEqual(list_jobs()[0]["status"], "ready")

    def test_process_job_uses_small_upload_batch_by_default(self):
        source = self.user_docs / "batch.txt"
        source.write_text("engine manual", encoding="utf-8")
        seen = {}

        def build(**kwargs):
            seen.update(kwargs)
            path = kwargs["source_paths"][0]
            database_path().parent.mkdir(parents=True, exist_ok=True)
            with contextlib.closing(connect(database_path())) as db:
                db.execute(
                    "INSERT OR REPLACE INTO documents(path,scope,status,error,updated_at) VALUES (?,?,?,?,?)",
                    (str(path), "user_docs", "indexed", "", "now"),
                )
                db.commit()
            return {"status": "completed"}

        enqueue(source, "user_docs")
        process_job(self.latest_job(), build=build)
        self.assertEqual(seen["batch_size"], 4)
        self.assertEqual(list_jobs()[0]["status"], "ready")

    def test_index_failure_message_explains_ollama_response_error(self):
        message = _index_failure_message(
            "failed",
            "ResponseError: status code: 502 bad gateway",
            {"current_chunks_total": 26, "current_chunks_done": 0},
        )
        self.assertIn("正文已提取 26 个片段", message)
        self.assertIn("Ollama 嵌入服务临时繁忙或超时", message)

    def test_index_failure_message_explains_missing_embedding_model(self):
        message = _index_failure_message(
            "failed",
            "ResponseError: model 'bge-m3' not found, try pulling it first",
            {"current_chunks_total": 4, "current_chunks_done": 0},
        )
        self.assertIn("Ollama 嵌入模型不可用", message)


if __name__ == "__main__":
    unittest.main()
