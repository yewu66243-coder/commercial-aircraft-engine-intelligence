"""Persistent upload-index jobs, consumed by one independent local worker."""
import contextlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

from .local_rag import get_local_rag_dir
from .library_rag import build_library_index, build_lock, database_path
from .local_library import (
    _upsert_index_entry, get_user_docs_dir, get_user_docs_index_path,
    get_papers_pool_dir, get_papers_index_path, get_patent_pool_dir, get_patents_index_path,
)

TARGETS = {
    'user_docs': (get_user_docs_dir, get_user_docs_index_path, '用户资料'),
    'all_papers_pool': (get_papers_pool_dir, get_papers_index_path, '论文'),
    'all_patent_pool': (get_patent_pool_dir, get_patents_index_path, '专利'),
}
ACTIVE = ('queued', 'waiting', 'indexing')


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@contextlib.contextmanager
def jobs_db():
    root = get_local_rag_dir()
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'upload_jobs.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, path TEXT, target TEXT, file_name TEXT,
        status TEXT, message TEXT, progress TEXT, created REAL, updated REAL)''')
    try:
        with db:
            yield db
    finally:
        db.close()


def _public(row):
    return {key: row[key] for key in ('id', 'file_name', 'target', 'status', 'message', 'updated')}


def list_jobs():
    with jobs_db() as db:
        rows = db.execute("SELECT * FROM jobs ORDER BY CASE WHEN status IN ('queued','waiting','indexing') THEN 0 ELSE 1 END,updated DESC LIMIT 100").fetchall()
    return [_public(row) for row in rows]


def enqueue(path, target):
    if target not in TARGETS:
        raise ValueError('Unknown library target')
    path = Path(path).resolve()
    if not path.is_relative_to(TARGETS[target][0]().resolve()) or not path.is_file():
        raise ValueError('File is no longer in the selected library')
    with jobs_db() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute("SELECT * FROM jobs WHERE path=? AND status IN ('queued','waiting','indexing') ORDER BY created DESC LIMIT 1", (str(path),)).fetchone()
        if existing:
            return _public(existing)
        job_id, now = uuid.uuid4().hex, time.time()
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',
                   (job_id, str(path), target, path.name, 'queued', '摘要索引已更新，全文索引等待处理', '{}', now, now))
        return _public(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())


def retry(job_id):
    with jobs_db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row['target'] not in TARGETS:
            raise ValueError('Unknown library target')
        path = Path(row['path']).resolve()
        if not path.is_relative_to(TARGETS[row['target']][0]().resolve()) or not path.is_file():
            raise ValueError('File is no longer in the selected library')
        if row['status'] in ACTIVE:
            return _public(row)
        db.execute("UPDATE jobs SET status='queued',message=?,progress=?,updated=? WHERE id=?",
                   ('已重新加入全文索引队列', '{}', time.time(), job_id))
        return _public(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())


def update(job_id, status, message, progress=None):
    with jobs_db() as db:
        db.execute('UPDATE jobs SET status=?,message=?,progress=?,updated=? WHERE id=?',
                   (status, message, json.dumps(progress or {}), time.time(), job_id))


def _index_failure_message(status, error='', result=None):
    if status == 'unsupported':
        return '该格式暂不支持正文索引，请转换为 PDF、DOCX 或 XLSX'
    if status == 'no_text':
        return '未提取到可用正文，请检查扫描清晰度、文件密码或手动转换后重试'

    result = result or {}
    last_error = result.get('last_error') if isinstance(result, dict) else {}
    detail = error or (last_error or {}).get('message') or (last_error or {}).get('type') or ''
    normalized = detail.lower()
    chunks_total = result.get('current_chunks_total', 0) if isinstance(result, dict) else 0
    chunks_done = result.get('current_chunks_done', 0) if isinstance(result, dict) else 0
    prefix = '正文已提取，但全文向量索引未写入'
    if chunks_total:
        prefix = f'正文已提取 {chunks_total} 个片段，已写入 {chunks_done} 个片段，但全文向量索引未完成'

    if any(token in normalized for token in ('model', 'not found', 'pull model', 'no such file')):
        return f'{prefix}：Ollama 嵌入模型不可用，请确认已安装并可调用 bge-m3 后点击重试。'
    if any(token in normalized for token in ('status code: 429', 'status code: 500', 'status code: 502',
                                             'status code: 503', 'status code: 504', 'bad gateway',
                                             'timeout', 'timed out', 'temporarily unavailable',
                                             'connection reset', 'server disconnected')):
        return f'{prefix}：Ollama 嵌入服务临时繁忙或超时，请等待全库索引/报告任务结束后重试。'
    if any(token in normalized for token in ('ollama', 'responseerror', 'embedding')):
        return f'{prefix}：Ollama 嵌入服务返回异常，请确认 Ollama 正在运行、bge-m3 可用后重试。'
    if detail:
        return f'{prefix}：{detail}'
    return '全文索引未完成，请检查文件是否可读及 Ollama 嵌入服务后重试'


def ensure_worker():
    directory = get_local_rag_dir() / 'upload_worker'
    try:
        with build_lock(directory):
            pass
    except RuntimeError:
        return
    script = Path(__file__).resolve().parents[2] / 'upload_index_worker.py'
    with (directory / 'worker.log').open('ab') as log:
        subprocess.Popen([sys.executable, '-u', str(script)], cwd=script.parent,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                         start_new_session=os.name != 'nt')


def process_job(row, build=build_library_index):
    def progress(state):
        if state.get('stage') == 'ocr':
            message = f"正在识别扫描正文：第 {state.get('ocr_page', 0)}/{state.get('ocr_total_pages', 0)} 页"
        elif state.get('stage') == 'embedding':
            message = f"正在建立全文索引：{state.get('current_chunks_done', 0)}/{state.get('current_chunks_total', 0)} 个片段"
        else:
            message = '正在读取资料正文'
        update(row['id'], 'indexing', message, state)
    try:
        path = Path(row['path'])
        if not path.is_file():
            raise FileNotFoundError()
        result = build(repair=True, source_paths=[path], progress=progress, state_name='upload-build',
                       chunk_size=_env_int('LOCAL_RAG_CHUNK_SIZE', 1000),
                       overlap=_env_int('LOCAL_RAG_CHUNK_OVERLAP', 120),
                       batch_size=max(1, _env_int('LOCAL_RAG_UPLOAD_BATCH_SIZE', 4)))
        with contextlib.closing(sqlite3.connect(database_path().as_uri() + '?mode=ro', uri=True)) as db:
            entry = db.execute('SELECT status,error FROM documents WHERE path=?', (str(path),)).fetchone()
        if result['status'] in ('paused', 'interrupted') or not entry or entry[0] != 'indexed':
            status = entry[0] if entry else 'failed'
            error = entry[1] if entry and len(entry) > 1 else ''
            message = _index_failure_message(status, error, result)
            update(row['id'], 'failed', message)
            return 'failed'
        root_fn, index_fn, source_type = TARGETS[row['target']]
        # Patent spreadsheets contain many abstract records; do not replace them with one file entry.
        if not (row['target'] == 'all_patent_pool' and path.suffix.lower() == '.xlsx'):
            try:
                _upsert_index_entry(index_fn(), path, source_type, root_fn())
            except Exception:
                logging.exception('Summary refresh failed for upload %s', row['id'])
                update(row['id'], 'partial', '全文索引已完成，摘要更新失败，请重试')
                return 'partial'
        update(row['id'], 'ready', '摘要与全文索引已完成，可参与检索')
        return 'ready'
    except RuntimeError as exc:
        if 'index build is already running' in str(exc):
            update(row['id'], 'waiting', '等待全库补建释放写入锁，随后自动处理')
            return 'waiting'
        else:
            logging.exception('Upload indexing failed')
            update(row['id'], 'failed', '索引处理异常，请检查后台日志后重试')
            return 'failed'
    except FileNotFoundError:
        update(row['id'], 'failed', '原文件已删除或移动，无法建立索引')
        return 'failed'
    except Exception:
        logging.exception('Upload indexing failed')
        update(row['id'], 'failed', '索引处理异常，请检查文件和嵌入服务后重试')
        return 'failed'


def run_worker():
    with build_lock(get_local_rag_dir() / 'upload_worker'):
        with jobs_db() as db:
            db.execute("UPDATE jobs SET status='queued',message='恢复未完成的索引任务' WHERE status='indexing'")
        idle_started = time.monotonic()
        idle_seconds = max(0.0, _env_float('UPLOAD_INDEX_WORKER_IDLE_SECONDS', 300.0))
        while True:
            with jobs_db() as db:
                row = db.execute("SELECT * FROM jobs WHERE status IN ('queued','waiting') ORDER BY created LIMIT 1").fetchone()
            if row:
                idle_started = time.monotonic()
                outcome = process_job(row)
                time.sleep(30 if outcome == 'waiting' else 3)
            else:
                if idle_seconds and time.monotonic() - idle_started >= idle_seconds:
                    return
                time.sleep(10)
