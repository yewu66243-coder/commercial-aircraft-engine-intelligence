# Word Derived PDF Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate formal-report PDF files from the saved Word document so DOCX and PDF share the same cover, TOC, pagination, figures, headers, and page numbers.

**Architecture:** Keep the current Markdown-to-DOCX renderer as the single formatting authority. Add a narrow DOCX-to-PDF renderer backed by Microsoft Word COM on Windows, expose it through the async file-writing layer, and change paired export entry points to generate Word before PDF without falling back to the independent HTML renderer.

**Tech Stack:** Python 3.11, python-docx, pywin32/Word COM, asyncio, unittest, pypdf, Microsoft Word 16.

---

## File map

- `backend/reporting/document_export.py`: synchronous Word COM conversion, field refresh, repagination, cleanup, and explicit error messages.
- `backend/utils.py`: asynchronous URL-safe `write_word_to_pdf` wrapper; existing independent `write_md_to_pdf` remains for PDF-only legacy callers.
- `three_agent_service.py`: primary Web workflow ordering and export-status integration.
- `backend/server/server_utils.py`: traditional server workflow ordering.
- `backend/server/app.py`: direct report endpoint ordering.
- `cli.py`: paired CLI ordering while retaining PDF-only behavior when DOCX is explicitly disabled.
- `tests/test_formal_export.py`: unit and Windows integration coverage for DOCX-derived PDF.
- `tests/test_formal_pipeline.py`: primary workflow regression coverage.
- `pyproject.toml`, `requirements.txt`, `backend/requirements.txt`: Windows-only `pywin32` dependency declaration.

### Task 1: Add the tested Word COM renderer

**Files:**
- Modify: `tests/test_formal_export.py`
- Modify: `backend/reporting/document_export.py`

- [ ] **Step 1: Write a failing unit test for the new renderer contract**

Add a test that replaces `pythoncom` and `win32com.client` with controlled fakes, then verifies that the renderer opens the exact DOCX, updates fields and TOC, repaginates, saves the Word file, exports PDF format 17, closes Word, and balances COM initialization:

Extend the test imports with `from types import ModuleType` and `from unittest.mock import MagicMock, Mock`.

```python
def test_docx_pdf_renderer_updates_and_exports_the_same_word_document(self):
    source = Path('outputs/source.docx').resolve()
    destination = Path('outputs/source.pdf').resolve()
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'docx')
    document = MagicMock()
    document.TablesOfContents = []
    document.Sections = []
    application = MagicMock()
    application.Documents.Open.return_value = document
    pythoncom = ModuleType('pythoncom')
    pythoncom.CoInitialize = Mock()
    pythoncom.CoUninitialize = Mock()
    win32com = ModuleType('win32com')
    client = ModuleType('win32com.client')
    client.DispatchEx = Mock(return_value=application)
    win32com.client = client
    modules = {'pythoncom': pythoncom, 'win32com': win32com, 'win32com.client': client}
    with patch.dict(sys.modules, modules), patch('backend.reporting.document_export.sys.platform', 'win32'):
        render_docx_pdf(source, destination)
    client.DispatchEx.assert_called_once_with('Word.Application')
    application.Documents.Open.assert_called_once_with(str(source), False, False, False)
    document.Fields.Update.assert_called_once()
    document.Repaginate.assert_called_once()
    document.Save.assert_called_once()
    document.ExportAsFixedFormat.assert_called_once_with(str(destination), 17)
    document.Close.assert_called_once_with(0)
    application.Quit.assert_called_once_with(0)
    pythoncom.CoInitialize.assert_called_once()
    pythoncom.CoUninitialize.assert_called_once()
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m unittest tests.test_formal_export.FormalExportTests.test_docx_pdf_renderer_updates_and_exports_the_same_word_document -v`

Expected: FAIL because `render_docx_pdf` does not exist.

- [ ] **Step 3: Implement the minimal synchronous Word renderer**

Add the following public function to `backend/reporting/document_export.py`:

```python
def render_docx_pdf(source: str | Path, destination: str | Path) -> None:
    if sys.platform != 'win32':
        raise RuntimeError('同版 PDF 导出需要 Windows 和 Microsoft Word。')
    try:
        import pythoncom
        from win32com.client import DispatchEx
    except ImportError as exc:
        raise RuntimeError('同版 PDF 导出需要 pywin32 和 Microsoft Word。') from exc

    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f'Word 文件不存在：{source_path}')
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    application = None
    document = None
    try:
        application = DispatchEx('Word.Application')
        application.Visible = False
        application.DisplayAlerts = 0
        document = application.Documents.Open(str(source_path), False, False, False)
        document.Fields.Update()
        for toc in document.TablesOfContents:
            toc.Update()
        for section in document.Sections:
            for header in section.Headers:
                header.Range.Fields.Update()
            for footer in section.Footers:
                footer.Range.Fields.Update()
        document.Repaginate()
        for toc in document.TablesOfContents:
            toc.UpdatePageNumbers()
        document.Save()
        document.ExportAsFixedFormat(str(destination_path), 17)
    except Exception as exc:
        raise RuntimeError('Microsoft Word 无法导出同版 PDF。') from exc
    finally:
        if document is not None:
            document.Close(0)
        if application is not None:
            application.Quit(0)
        pythoncom.CoUninitialize()
```

- [ ] **Step 4: Run the unit test and existing formal-export tests**

Run: `python -m unittest tests.test_formal_export -v`

Expected: the new test and all existing formal-export tests pass.

- [ ] **Step 5: Commit the renderer**

```bash
git add backend/reporting/document_export.py tests/test_formal_export.py
git commit -m "feat: export PDF through Microsoft Word"
```

### Task 2: Add the async DOCX-to-PDF writer and dependencies

**Files:**
- Modify: `tests/test_formal_export.py`
- Modify: `backend/utils.py`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Write failing async-wrapper tests**

Add tests that pass a URL-encoded Word path and verify conversion, plus a failure test that returns the existing empty-string contract without invoking the HTML renderer:

```python
def test_word_pdf_writer_decodes_the_docx_path(self):
    encoded = quote('outputs/中文 报告.docx')
    with patch('backend.utils.render_docx_pdf') as renderer:
        result = asyncio.run(write_word_to_pdf(encoded, '中文 报告'))
    renderer.assert_called_once_with(Path('outputs/中文 报告.docx'), Path('outputs/中文 报告.pdf'))
    self.assertEqual(result, quote('outputs/中文 报告.pdf'))

def test_word_pdf_writer_returns_empty_when_word_is_unavailable(self):
    with patch('backend.utils.render_docx_pdf', side_effect=RuntimeError('Microsoft Word unavailable')):
        result = asyncio.run(write_word_to_pdf('outputs/report.docx', 'report'))
    self.assertEqual(result, '')
```

- [ ] **Step 2: Run the wrapper tests and verify RED**

Run: `python -m unittest tests.test_formal_export.FormalExportTests.test_word_pdf_writer_decodes_the_docx_path tests.test_formal_export.FormalExportTests.test_word_pdf_writer_returns_empty_when_word_is_unavailable -v`

Expected: FAIL because `write_word_to_pdf` is not defined.

- [ ] **Step 3: Implement the async wrapper**

Import `render_docx_pdf` and add this function to `backend/utils.py`:

```python
async def write_word_to_pdf(word_path: str, filename: str = '') -> str:
    if not word_path:
        return ''
    source = Path(urllib.parse.unquote(word_path))
    destination = Path(f'outputs/{filename[:60]}.pdf')
    try:
        await asyncio.to_thread(render_docx_pdf, source, destination)
        print(f'Report written to {destination}')
        return urllib.parse.quote(str(destination).replace('\\', '/'))
    except Exception as exc:
        print(f'Error in converting Word to PDF: {exc}')
        return ''
```

- [ ] **Step 4: Declare the conditional Windows dependency**

Add `pywin32>=311 ; sys_platform == 'win32'` to both dependency tables in `pyproject.toml` and to the document/output sections of `requirements.txt` and `backend/requirements.txt`.

- [ ] **Step 5: Run wrapper and export tests**

Run: `python -m unittest tests.test_formal_export -v`

Expected: all formal-export tests pass.

- [ ] **Step 6: Commit the async writer**

```bash
git add backend/utils.py tests/test_formal_export.py pyproject.toml requirements.txt backend/requirements.txt
git commit -m "feat: add Word-derived PDF writer"
```

### Task 3: Change the primary 3-Agent workflow to Word-first export

**Files:**
- Modify: `tests/test_formal_pipeline.py`
- Modify: `three_agent_service.py`

- [ ] **Step 1: Write a failing workflow-order test**

Extend the mocked pipeline test so the Word writer returns `outputs/report.docx`, the PDF writer is `write_word_to_pdf`, and the assertion requires that exact path:

```python
word = stack.enter_context(patch(
    'three_agent_service.write_md_to_word',
    new=AsyncMock(return_value='outputs/report.docx'),
))
pdf = stack.enter_context(patch(
    'three_agent_service.write_word_to_pdf',
    new=AsyncMock(return_value='outputs/report.pdf'),
))
result = asyncio.run(service.run())
self.assertEqual(pdf.call_args.args[0], 'outputs/report.docx')
self.assertEqual(word.call_args.args[0], md.call_args.args[0])
self.assertTrue(result['export_status']['pdf'])
```

Add a second assertion path with `write_md_to_word` returning `''`; `write_word_to_pdf` must not be called and PDF export status must be false.

- [ ] **Step 2: Run the pipeline tests and verify RED**

Run: `python -m unittest tests.test_formal_pipeline -v`

Expected: FAIL because `three_agent_service` still invokes `write_md_to_pdf` before Word.

- [ ] **Step 3: Implement Word-first ordering**

Change the import and export block in `three_agent_service.py` to:

```python
from backend.utils import write_text_to_md, write_md_to_word, write_word_to_pdf

md_path = await write_text_to_md(final_report, filename)
word_path = await write_md_to_word(final_report, filename)
pdf_path = await write_word_to_pdf(word_path, filename) if word_path else ''
```

Keep the existing `export_status`, error summaries, and run statistics unchanged so the UI receives an empty PDF path when Microsoft Word is unavailable.

- [ ] **Step 4: Run primary pipeline tests**

Run: `python -m unittest tests.test_formal_pipeline -v`

Expected: all primary pipeline tests pass.

- [ ] **Step 5: Commit the primary workflow change**

```bash
git add three_agent_service.py tests/test_formal_pipeline.py
git commit -m "fix: derive 3-Agent PDF from Word"
```

### Task 4: Align remaining paired export entry points

**Files:**
- Modify: `backend/server/server_utils.py`
- Modify: `backend/server/app.py`
- Modify: `cli.py`
- Modify: `tests/test_formal_pipeline.py`

- [ ] **Step 1: Write failing call-order tests for paired helpers**

Add tests that patch `write_md_to_word` and `write_word_to_pdf` and verify `backend.server.server_utils.generate_report_files` returns the Word-derived PDF path. Add a source-level CLI assertion that the paired path calls `write_word_to_pdf(docx_path, shared_stem)` and only the explicit `--no-docx` branch can call `write_md_to_pdf`.

```python
async def exercise():
    with patch('backend.server.server_utils.write_md_to_word', new=AsyncMock(return_value='outputs/report.docx')) as word, \
         patch('backend.server.server_utils.write_word_to_pdf', new=AsyncMock(return_value='outputs/report.pdf')) as pdf, \
         patch('backend.server.server_utils.write_text_to_md', new=AsyncMock(return_value='outputs/report.md')):
        result = await generate_report_files('# Report', 'report')
    self.assertEqual(result['docx'], 'outputs/report.docx')
    self.assertEqual(result['pdf'], 'outputs/report.pdf')
    pdf.assert_awaited_once_with('outputs/report.docx', 'report')
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `python -m unittest tests.test_formal_pipeline -v`

Expected: FAIL because the remaining paired entry points still call the Markdown PDF writer.

- [ ] **Step 3: Update server helpers and the direct report endpoint**

Import `write_word_to_pdf`, generate Word first, and pass its returned path to the new writer:

```python
docx_path = await write_md_to_word(report, filename)
pdf_path = await write_word_to_pdf(docx_path, filename) if docx_path else ''
md_path = await write_text_to_md(report, filename)
```

Apply the same ordering to the direct endpoint in `backend/server/app.py`.

- [ ] **Step 4: Update paired CLI behavior**

When DOCX is enabled, create it before PDF and call `write_word_to_pdf`. Retain `write_md_to_pdf` only when the user explicitly requests PDF while disabling DOCX:

```python
docx_path = ''
if not args.no_docx:
    docx_path = await write_md_to_word(final_markdown, shared_stem)
if not args.no_pdf:
    pdf_path = (
        await write_word_to_pdf(docx_path, shared_stem)
        if docx_path
        else await write_md_to_pdf(final_markdown, shared_stem)
    )
```

- [ ] **Step 5: Run pipeline and export tests**

Run: `python -m unittest tests.test_formal_pipeline tests.test_formal_export -v`

Expected: all tests pass.

- [ ] **Step 6: Commit remaining entry points**

```bash
git add backend/server/server_utils.py backend/server/app.py cli.py tests/test_formal_pipeline.py
git commit -m "fix: use Word-first paired exports"
```

### Task 5: Regenerate and visually verify the `df74` PDF

**Files:**
- Source: `C:/Users/吴烨/Desktop/研究生项目/国标/GTF发动机技术问题与市场影响跟踪专题研究报告：PW1000_df74.docx`
- Replace: `C:/Users/吴烨/Desktop/研究生项目/国标/GTF发动机技术问题与市场影响跟踪专题研究报告：PW1000_df74.pdf`

- [ ] **Step 1: Back up the old mismatched PDF without deleting it**

Copy the existing PDF to the same directory with suffix `.before-word-derived.pdf` so the previous artifact remains recoverable.

- [ ] **Step 2: Export the attached DOCX through the new renderer**

Run the repository Python interpreter and call:

```python
from backend.reporting.document_export import render_docx_pdf
render_docx_pdf(
    r'C:\Users\吴烨\Desktop\研究生项目\国标\GTF发动机技术问题与市场影响跟踪专题研究报告：PW1000_df74.docx',
    r'C:\Users\吴烨\Desktop\研究生项目\国标\GTF发动机技术问题与市场影响跟踪专题研究报告：PW1000_df74.pdf',
)
```

- [ ] **Step 3: Verify document structure**

Use Microsoft Word `ComputeStatistics(2)` and pypdf. Expected: both files have 9 pages; the PDF is A4 and contains the report title, abstract, sections 1–7, and references.

- [ ] **Step 4: Render and inspect all PDF pages**

Render the latest PDF to `tmp/pdfs/df74/page-*.png` with bundled Poppler. Inspect all nine pages at 100% and confirm the cover, TOC, body, figure, references, headers, and footers match the Word render with no clipping or overlap.

- [ ] **Step 5: Run the full relevant test suite**

Run: `python -m unittest tests.test_formal_export tests.test_formal_pipeline tests.test_report_finalization -v`

Expected: all tests pass.

- [ ] **Step 6: Restart and smoke-test the local service**

Restart only the current workspace's uvicorn process, then verify `/`, `/api/model-providers`, and `/api/local-library` return HTTP 200 and preserve DeepSeek as the default with Qwen configured.

- [ ] **Step 7: Final repository check**

Run: `git status --short` and `git log -5 --oneline`.

Expected: only diagnostic files under `tmp/` may remain untracked; all implementation changes are committed.
