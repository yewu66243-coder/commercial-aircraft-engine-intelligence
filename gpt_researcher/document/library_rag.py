"""Incremental, disk-backed full-library retrieval using the configured embeddings."""
from __future__ import annotations

import contextlib
import heapq
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

import numpy as np

from .local_library import get_papers_pool_dir, get_patent_pool_dir, get_user_docs_dir
from .local_rag import (
    LocalRagChunk, LocalRagMatch, RAG_INDEX_SCHEMA_VERSION, _atomic_text,
    _default_embeddings, _embedding_identity, _embed_documents, _embed_query,
    _file_sha256, _keyword_bonus, _query_terms, chunk_source,
    format_rag_context, get_local_rag_dir,
)

SCOPES = {"papers": "论文", "patents": "专利", "user_docs": "用户资料"}
SUPPORTED = {".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx"}


def safe_error_message(error: BaseException, *, limit: int = 500) -> str:
    """Keep enough local-index failure detail for UI diagnosis without leaking secrets."""
    text = f"{type(error).__name__}: {error}".strip()
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*\S+", r"\1=<redacted>", text)
    text = re.sub(r"(?i)(sk-[A-Za-z0-9_-]{8,})", "<redacted>", text)
    text = " ".join(text.split())
    return text[:limit]


def library_roots():
    return {"papers": get_papers_pool_dir(), "patents": get_patent_pool_dir(),
            "user_docs": get_user_docs_dir()}


def database_path():
    return get_local_rag_dir() / "library.sqlite3"


def inventory(roots=None):
    sources = []
    seen = set()
    for scope, root in (roots if roots is not None else library_roots()).items():
        if not Path(root).is_dir():
            raise FileNotFoundError(f"Library directory missing: {root}")
        for path in sorted(Path(root).rglob("*")):
            if not path.is_file() or path.name.startswith(("~$", ".")):
                continue
            path = path.resolve()
            if str(path) in seen:
                continue
            seen.add(str(path))
            sources.append({"source_path": str(path), "file_name": path.name,
                            "title": path.stem, "scope": scope, "source_type": SCOPES[scope]})
    return sources


@contextlib.contextmanager
def build_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "library-build.lock").open("a+b") as handle:
        handle.seek(0)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("A full-library index build is already running") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            path TEXT PRIMARY KEY, scope TEXT NOT NULL, digest TEXT, signature TEXT,
            size INTEGER, mtime INTEGER, status TEXT, error TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY, path TEXT REFERENCES documents(path) ON DELETE CASCADE,
            metadata TEXT NOT NULL, vector BLOB NOT NULL);
        CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
        CREATE INDEX IF NOT EXISTS documents_scope ON documents(scope, status);
    """)
    return db


def build_library_index(*, roots=None, embeddings=None, progress=None, chunk_size=1000,
                        overlap=120, batch_size=16, limit=None, repair=False, source_paths=None,
                        state_name="library-build"):
    if chunk_size <= 0 or not 0 <= overlap < chunk_size or batch_size <= 0:
        raise ValueError("Invalid chunk or batch settings")
    directory = get_local_rag_dir()
    with build_lock(directory):
        sources = inventory(roots)
        selected = sources
        if repair:
            with contextlib.closing(connect(database_path())) as selection_db:
                pending = {row[0] for row in selection_db.execute("SELECT path FROM documents WHERE status != 'indexed'")}
            if source_paths is not None:
                pending.update(str(Path(path).resolve()) for path in source_paths)
            from .text_recovery import CACHE_VERSION
            for cached in (directory / "recovered_text").glob("*.json"):
                data = json.loads(cached.read_text(encoding="utf-8"))
                if data.get("complete") and data.get("method") == "ocr" and data.get("version") != CACHE_VERSION:
                    pending.add(data.get("source_path"))
            selected = [source for source in sources if source["source_path"] in pending]
        if source_paths is not None:
            requested = {str(Path(path).resolve()) for path in source_paths}
            selected = [source for source in selected if source["source_path"] in requested]
        if limit is not None:
            selected = selected[:max(0, limit)]
        signature = json.dumps([_embedding_identity(embeddings), chunk_size, overlap,
                                RAG_INDEX_SCHEMA_VERSION])
        client = embeddings
        state = {"status": "running", "pid": os.getpid(), "total_files": len(sources),
                 "run_files": len(selected), "processed": 0, "indexed": 0,
                 "reused": 0, "failed": 0, "unsupported": 0, "new_chunks": 0,
                 "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        state["repair"] = repair
        stop_path = directory / f"{state_name}.stop"
        stop_path.unlink(missing_ok=True)

        def publish():
            state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _atomic_text(directory / f"{state_name}-status.json",
                         json.dumps(state, ensure_ascii=False, indent=2))
            if progress:
                progress(dict(state))

        db = connect(database_path())
        publish()
        try:
            for source in selected:
                if stop_path.exists():
                    state["status"] = "paused"
                    break
                path = Path(source["source_path"])
                key = str(path)
                state["current_file"] = key
                state["current_chunks_done"] = 0
                state["current_chunks_total"] = 0
                state.update(ocr_page=0, ocr_total_pages=0, recovered_empty_pages=[], stage="reading")
                publish()
                try:
                    stat = path.stat()
                    digest = _file_sha256(path)
                    old = db.execute("SELECT digest,signature,status FROM documents WHERE path=?", (key,)).fetchone()
                    from .text_recovery import read_recovered_text, cache_path
                    stale_recovery = cache_path(path, digest).exists() and read_recovered_text(path, digest) is None
                    if old == (digest, signature, "indexed") and not stale_recovery:
                        with db:
                            db.execute("UPDATE documents SET size=?,mtime=?,scope=? WHERE path=?",
                                       (stat.st_size, stat.st_mtime_ns, source["scope"], key))
                        state["reused"] += 1
                        state["processed"] += 1
                        publish()
                        continue
                    from .text_recovery import read_recovered_text
                    recoverable_doc = path.suffix.lower() == ".doc" and (repair or read_recovered_text(path))
                    if path.suffix.lower() not in SUPPORTED and not recoverable_doc:
                        records, status, error = [], "unsupported", "unsupported_format"
                    else:
                        records = chunk_source(source, chunk_size=chunk_size, overlap=overlap)
                        if repair and not records and path.suffix.lower() in {".pdf", ".doc"}:
                            from .text_recovery import recover_source
                            def report_page(done, total):
                                state.update(ocr_page=done + 1, ocr_total_pages=total, stage="ocr")
                                publish()
                            recovery = recover_source(path, progress=report_page, stopped=stop_path.exists)
                            state["recovered_empty_pages"] = recovery.get("empty_pages", [])
                            records = chunk_source(source, chunk_size=chunk_size, overlap=overlap)
                        status = "indexed" if records else "no_text"
                        error = "" if records else "no_extractable_text_or_parse_failure"
                    state["current_chunks_total"] = len(records)
                    state["stage"] = "embedding"
                    publish()
                    now = time.strftime("%Y-%m-%d %H:%M:%S")
                    # A document replacement is one transaction; partial vectors never become searchable.
                    with db:
                        db.execute("DELETE FROM documents WHERE path=?", (key,))
                        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)",
                                   (key, source["scope"], digest, signature, stat.st_size,
                                    stat.st_mtime_ns, status, error, now))
                        dimension = None
                        for start in range(0, len(records), batch_size):
                            if stop_path.exists():
                                raise InterruptedError("Index build paused")
                            if client is None:
                                client = _default_embeddings()
                            batch = records[start:start + batch_size]
                            vectors = _embed_documents(client, [record["text"] for record in batch], batch_size=batch_size)
                            array = np.asarray(vectors, dtype=np.float32)
                            if (array.ndim != 2 or len(array) != len(batch) or array.shape[1] == 0
                                    or not np.isfinite(array).all() or np.any(np.linalg.norm(array, axis=1) == 0)):
                                raise ValueError("Invalid embedding vectors")
                            if dimension is not None and dimension != array.shape[1]:
                                raise ValueError("Embedding dimensions changed during indexing")
                            dimension = array.shape[1]
                            array /= np.linalg.norm(array, axis=1, keepdims=True)
                            for record, vector in zip(batch, array):
                                chunk = LocalRagChunk(**record, embedding=[], sha256=digest, updated_at=now)
                                db.execute("INSERT INTO chunks VALUES (?,?,?,?)",
                                           (chunk.chunk_id, key, json.dumps(chunk.__dict__, ensure_ascii=False), vector.tobytes()))
                            state["current_chunks_done"] += len(batch)
                            publish()
                        current = path.stat()
                        if (stat.st_size, stat.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
                            raise ValueError("Source changed during indexing; retry on next run")
                    state["indexed" if status == "indexed" else "unsupported" if status == "unsupported" else "failed"] += 1
                    state["new_chunks"] += len(records)
                except InterruptedError:
                    state["status"] = "paused"
                    break
                except Exception as exc:
                    state["failed"] += 1
                    error_text = safe_error_message(exc)
                    logging.exception("Full-library index failed for %s", key)
                    state["last_error"] = {"file": key, "type": type(exc).__name__, "message": error_text}
                    with db:
                        db.execute("DELETE FROM documents WHERE path=?", (key,))
                        db.execute("INSERT INTO documents(path,scope,status,error,updated_at) VALUES (?,?,?,?,?)",
                                   (key, source["scope"], "failed", error_text, time.strftime("%Y-%m-%d %H:%M:%S")))
                    # Stop a broken embedding service from failing thousands of documents.
                    if state["current_chunks_total"] and state["current_chunks_done"] < state["current_chunks_total"]:
                        state["status"] = "interrupted"
                        publish()
                        break
                state["processed"] += 1
                publish()
            if state["status"] == "running":
                state["status"] = "completed_with_errors" if state["failed"] or state["unsupported"] else "completed"
                if limit is None and not repair and source_paths is None:
                    allowed = {source["source_path"] for source in sources}
                    with db:
                        for (path,) in db.execute("SELECT path FROM documents").fetchall():
                            if path not in allowed:
                                db.execute("DELETE FROM documents WHERE path=?", (path,))
            state["document_status_counts"] = dict(db.execute("SELECT status,COUNT(*) FROM documents GROUP BY status"))
            if state["status"] == "completed" and any(
                    status != "indexed" and count for status, count in state["document_status_counts"].items()):
                state["status"] = "completed_with_errors"
            state["total_chunks"] = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            publish()
            return state
        except BaseException as exc:
            state["status"] = "interrupted"
            state["last_error"] = {"type": type(exc).__name__}
            publish()
            raise
        finally:
            db.close()


def search_library(query, *, scopes, max_chunks=24, max_per_document=3, embeddings=None, source_paths=None):
    path = database_path()
    scopes = sorted(set(scopes) & SCOPES.keys())
    empty = {"matches": [], "context": "", "chunk_count": 0}
    if not path.exists() or not scopes or max_chunks <= 0:
        return empty
    identity = _embedding_identity(embeddings)
    allowed = None if source_paths is None else {str(Path(p).resolve()) for p in source_paths}
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        documents = {}
        for key, size, mtime, signature in db.execute(
                "SELECT path,size,mtime,signature FROM documents WHERE status='indexed' AND scope IN ("
                + ",".join("?" for _ in scopes) + ")", scopes):
            if allowed is not None and key not in allowed:
                continue
            if json.loads(signature)[0] != identity:
                continue
            try:
                stat = Path(key).stat()
                if (stat.st_size, stat.st_mtime_ns) == (size, mtime):
                    documents[key] = True
            except OSError:
                continue
        if not documents:
            return empty
        client = embeddings or _default_embeddings()
        vector = np.asarray(_embed_query(client, query), dtype=np.float32)
        if vector.ndim != 1 or not np.isfinite(vector).all() or not np.linalg.norm(vector):
            raise ValueError("Invalid query embedding")
        vector /= np.linalg.norm(vector)
        terms = _query_terms(query)
        heap, serial, count = [], 0, 0
        # A bounded per-document shortlist avoids one long PDF occupying every result.
        for key in documents:
            best = []
            cursor = db.execute("SELECT metadata,vector FROM chunks WHERE path=?", (key,))
            while rows := cursor.fetchmany(128):
                metadata = [json.loads(row[0]) for row in rows]
                matrix = np.stack([np.frombuffer(row[1], dtype=np.float32) for row in rows])
                if matrix.shape[1] != len(vector):
                    raise ValueError("Index vector dimensions differ; rebuild index")
                scores = matrix @ vector
                count += len(rows)
                for item, score in zip(metadata, scores):
                    score = float(score) + _keyword_bonus(terms, item["text"])
                    if score <= 0:
                        continue
                    serial += 1
                    entry = (score, serial, item)
                    heapq.heappush(best, entry)
                    if len(best) > max_per_document:
                        heapq.heappop(best)
            for entry in best:
                heapq.heappush(heap, entry)
                if len(heap) > max_chunks:
                    heapq.heappop(heap)
        matches = [LocalRagMatch(score, LocalRagChunk(**item))
                   for score, _, item in sorted(heap, reverse=True)]
        return {"matches": matches, "context": format_rag_context(matches), "chunk_count": count,
                "indexed_paths": list(documents)}
    finally:
        db.close()


def extend_selection_with_matches(selection, matches, max_documents=5):
    import shutil
    import hashlib
    from dataclasses import asdict
    from .local_index import SelectedLocalPaper

    directory = Path(selection.doc_path)
    directory.mkdir(parents=True, exist_ok=True)
    existing = {str(Path(item.source_path).resolve()) for item in selection.selected}
    added = 0
    for match in matches:
        path = Path(match.chunk.source_path).resolve()
        if str(path) in existing or added >= max_documents or not path.is_file():
            continue
        filename = path.name
        if (directory / filename).exists():
            suffix = hashlib.sha256(str(path).encode()).hexdigest()[:10]
            filename = f"{path.stem}_{suffix}{path.suffix}"
        shutil.copy2(path, directory / filename)
        selection.selected.append(SelectedLocalPaper(
            match.chunk.title, filename, str(path), match.score,
            source_type=match.chunk.source_type,
        ))
        existing.add(str(path))
        added += 1
    _atomic_text(directory / "selected_sources.json", json.dumps(
        [asdict(item) for item in selection.selected], ensure_ascii=False, indent=2))
    return added
