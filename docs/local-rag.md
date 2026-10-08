# Local document RAG

The three-agent service now uses two-stage local retrieval: existing metadata
selection followed by persisted body-chunk retrieval within the selected files.
Retrieved excerpts are passed to the writer with original filenames and PDF page
numbers. Excerpt labels are retrieval identifiers, not bibliography numbers.
Original documents remain the source used by evidence verification.

The on-demand implementation is complemented by a full-library SQLite index.
Run `portable_python\python.exe build_local_rag.py --build` from the project root
to recursively index the paper pool, patent pool and user document pool.
The command resumes at completed documents on the next run; modified documents
are rebuilt and missing documents are removed after a complete inventory pass.
Use `--inventory` to count files, `--status` to read progress, and `--stop` to
request a pause at the next embedding batch. `--limit N` is a partial test run.

The database is `local_docs/rag_index/library.sqlite3`. A cross-process build
lock prevents duplicate builders; WAL transactions allow reports to search
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
LOCAL_RAG_ENABLED=true
LOCAL_RAG_CHUNK_SIZE=1000
LOCAL_RAG_CHUNK_OVERLAP=120
LOCAL_RAG_MAX_CONTEXT_CHUNKS=18
```

Chunk lengths are characters, not tokens. LOCAL_RAG_INDEX_DIR optionally overrides
local_docs/rag_index. The index contains local document text and embeddings and is
excluded from Git. Embedding requests use the configured embedding provider;
with a remote provider, document chunks are sent to that provider.

The index uses JSONL and exact cosine ranking with a small lexical-match bonus.
It suits the bounded set of documents selected per task. Atomic file replacement
and an in-process lock protect concurrent tasks within one server process; shared
index writes from multiple server processes are not supported.

The task log reports indexing and retrieval stages. The evaluation record's
local_rag field records index counts, failures and retrieved file/page/chunk IDs.
If indexing or embedding fails, the task continues using existing local document
reading and logs the fallback. A first retrieval takes longer because embedding
results have not yet been cached.
