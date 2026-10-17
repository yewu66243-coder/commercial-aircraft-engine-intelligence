# Local document RAG

## Current Workbench Behavior

The workbench uses title/abstract metadata selection and reads the selected
original documents. Report-time vector retrieval and upload-time full-text
indexing have been removed from the workbench, including their environment
switches, queue routes and frontend controls. Uploads update summary indexes.
Existing full-text databases and build/job state were deleted on 2026-10-15.
OCR text caches and summary-index locks remain because summary extraction and
source verification still use them; they are not vector indexes.

## Legacy Standalone Tools

The following describes retained standalone tools, not the active report or
upload workflow. Running a build command explicitly will recreate index data.

The on-demand implementation is complemented by a full-library SQLite index.
Run `portable_python\python.exe build_local_rag.py --build` from the project root
to recursively index the paper pool, patent pool and user document pool.
The command resumes at completed documents on the next run; modified documents
are rebuilt and missing documents are removed after a complete inventory pass.
Use `--inventory` to count files, `--status` to read progress, and `--stop` to
request a pause at the next embedding batch. `--limit N` is a partial test run.

The database is `local_docs/rag_index/library.sqlite3`. A cross-process build
lock prevents duplicate builders; WAL transactions allow standalone searches of
completed documents while the batch build continues. Each document is committed
atomically. A stopped document is rebuilt on resume. No-text PDFs and unsupported
formats are recorded separately; an embedding-service failure interrupts the
batch instead of marking the entire library failed. XLSX worksheets are read
with openpyxl. Use `--build --repair` or `--background --repair` to retry pending
documents with local RapidOCR for PDFs and read-only Word COM for legacy DOC.
Recovered text is content-hash cached with original page numbers and shared by
retrieval, document loading and source verification. Legacy XLS remains unsupported.

Full-library search filters by user-selected scopes and excludes missing or
modified files and embeddings from another model. It supplements metadata
selection with up to max_docs additional documents. All selected sources are
still available to the existing original-document verification pipeline.
Full-library vectors are reused for selected documents; the original JSONL index
is now only the on-demand fallback for documents not yet indexed in SQLite.
Ranking uses exact cosine similarity and a lexical bonus, with a per-document
result limit. It is not an ANN or learned-reranker implementation.

The on-demand fallback operates as follows:
It builds the selected documents on demand, reuses unchanged document vectors,
and rebuilds when file contents, chunk settings or embedding model change.
PDF text, DOCX paragraphs and tables, TXT, MD and CSV are supported. Scanned PDFs
use cached text after the explicit repair step. Unsupported or unreadable files are recorded as
indexing errors rather than treated as successfully indexed.

Configuration uses the existing EMBEDDING provider/model. Optional environment
variables (defaults shown):

```dotenv
LOCAL_RAG_CHUNK_SIZE=1000
LOCAL_RAG_CHUNK_OVERLAP=120
```

Chunk lengths are characters, not tokens. LOCAL_RAG_INDEX_DIR optionally overrides
local_docs/rag_index. The index contains local document text and embeddings and is
excluded from Git. Embedding requests use the configured embedding provider;
with a remote provider, document chunks are sent to that provider.

The index uses JSONL and exact cosine ranking with a small lexical-match bonus.
It suits the bounded set of documents selected per task. Atomic file replacement
and an in-process lock protect concurrent tasks within one server process; shared
index writes from multiple server processes are not supported.

New workbench report records do not contain local_rag or library_rag fields.
Historical report records are preserved and may still contain those fields.
