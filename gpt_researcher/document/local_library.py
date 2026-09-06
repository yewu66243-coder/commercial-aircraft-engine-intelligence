from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, BinaryIO
from xml.etree import ElementTree


SUPPORTED_LIBRARY_EXTENSIONS = {".pdf", ".txt", ".md", ".doc", ".docx", ".xlsx", ".xls", ".csv"}
SUPPORTED_TEXT_DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".md", ".doc", ".docx"}
SUPPORTED_PATENT_INDEX_TABLE_EXTENSIONS = {".xlsx"}
PAPER_INDEX_NAME = "papers_index.json"
USER_DOCS_INDEX_NAME = "user_docs_index.json"
PATENT_INDEX_NAME = "patents_index.json"
PAPER_SOURCE_TYPE = "\u8bba\u6587"
USER_DOC_SOURCE_TYPE = "\u7528\u6237\u8d44\u6599"
PATENT_SOURCE_TYPE = "\u4e13\u5229"
FILENAME_FALLBACK_PREFIX = "\u6587\u4ef6\u540d\uff1a"


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_local_docs_root() -> Path:
    return Path(os.getenv("LOCAL_DOCS_PATH", get_project_root() / "local_docs")).resolve()


def get_user_docs_dir() -> Path:
    return (get_local_docs_root() / "user_docs").resolve()


def get_papers_pool_dir() -> Path:
    return Path(os.getenv("LOCAL_DOCS_POOL_PATH", get_local_docs_root() / "all_papers_pool")).resolve()


def get_patent_pool_dir() -> Path:
    return Path(os.getenv("LOCAL_PATENT_POOL_PATH", get_local_docs_root() / "all_patent_pool")).resolve()


def get_user_docs_index_path() -> Path:
    return (get_local_docs_root() / USER_DOCS_INDEX_NAME).resolve()


def get_papers_index_path() -> Path:
    return Path(os.getenv("LOCAL_DOCS_INDEX_PATH", get_local_docs_root() / PAPER_INDEX_NAME)).resolve()


def get_patents_index_path() -> Path:
    return Path(os.getenv("LOCAL_PATENT_INDEX_PATH", get_local_docs_root() / PATENT_INDEX_NAME)).resolve()


def ensure_local_library_dirs() -> None:
    get_local_docs_root().mkdir(parents=True, exist_ok=True)
    get_user_docs_dir().mkdir(parents=True, exist_ok=True)
    get_papers_pool_dir().mkdir(parents=True, exist_ok=True)
    get_patent_pool_dir().mkdir(parents=True, exist_ok=True)
    for index_path in (get_user_docs_index_path(), get_papers_index_path(), get_patents_index_path()):
        if not index_path.exists():
            _write_json(index_path, [])


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _write_json(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def _safe_filename(filename: str) -> str:
    name = Path(filename or "uploaded_document").name
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip(" .")
    return name or f"uploaded_{int(time.time())}.txt"


def _dedupe_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 1000):
        next_candidate = directory / f"{stem}_{index}{suffix}"
        if not next_candidate.exists():
            return next_candidate
    return directory / f"{stem}_{int(time.time())}{suffix}"


def _copy_file_with_hash(fileobj: BinaryIO, destination: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with destination.open("wb") as handle:
        while True:
            chunk = fileobj.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_existing_file_by_hash(
    directory: Path,
    digest: str,
    size: int,
    skip_path: Path | None = None,
) -> Path | None:
    if not directory.exists():
        return None

    resolved_skip = skip_path.resolve() if skip_path else None
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_LIBRARY_EXTENSIONS:
            continue
        if resolved_skip and path.resolve() == resolved_skip:
            continue
        if path.stat().st_size != size:
            continue
        try:
            if _file_sha256(path) == digest:
                return path
        except OSError:
            continue
    return None


def _normalize_lookup_text(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


COPY_SUFFIX_RE = re.compile(r"\s*\((\d+)\)$")


def _stem_without_copy_suffix(path: Path) -> str:
    return COPY_SUFFIX_RE.sub("", path.stem).strip()


def _title_candidates_from_filename(path: Path) -> list[str]:
    stem = _stem_without_copy_suffix(path)
    candidates = [stem]
    markers = [
        "\u005f\u672c\u62a5\u8bb0\u8005",
        "\u005f\u8bb0\u8005",
        "\u005f\u901a\u8baf\u5458",
        "\u005f\u6d1b\u62a5\u878d\u5a92\u8bb0\u8005",
        "\u005f\u4e09\u6e58\u90fd\u5e02\u62a5\u5168\u5a92\u4f53\u8bb0\u8005",
    ]
    for marker in markers:
        if marker in stem:
            candidates.append(stem.split(marker, 1)[0])
    if "_" in stem:
        candidates.append(stem.rsplit("_", 1)[0])
    if "__" in stem:
        candidates.append(stem.split("__", 1)[0])

    result: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip(" _-")
        if candidate and candidate not in result:
            result.append(candidate)
    return result or [path.stem]


def _author_from_filename(path: Path) -> str:
    stem = _stem_without_copy_suffix(path)
    if "_" not in stem:
        return ""
    tail = stem.rsplit("_", 1)[1].strip(" _-")
    if not tail or len(tail) > 40:
        return ""
    if any(ch.isalpha() for ch in tail):
        return tail.replace("__", "; ")
    return ""


def _xlsx_node_text(node: ElementTree.Element) -> str:
    return "".join(child.text or "" for child in node.iter() if child.tag.endswith("}t"))


def _xlsx_col_index(cell_ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return max(index - 1, 0)


def _read_first_xlsx_sheet(path: Path) -> list[list[str]]:
    try:
        with zipfile.ZipFile(path) as workbook:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in workbook.namelist():
                shared_root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
                shared_strings = [_xlsx_node_text(item) for item in shared_root if item.tag.endswith("}si")]

            sheet_name = "xl/worksheets/sheet1.xml"
            if sheet_name not in workbook.namelist():
                sheet_names = [
                    name
                    for name in workbook.namelist()
                    if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
                ]
                if not sheet_names:
                    return []
                sheet_name = sorted(sheet_names)[0]

            sheet_root = ElementTree.fromstring(workbook.read(sheet_name))
    except Exception:
        return []

    rows: list[list[str]] = []
    for row in sheet_root.iter():
        if not row.tag.endswith("}row"):
            continue
        values: list[str] = []
        for cell in row:
            if not cell.tag.endswith("}c"):
                continue
            cell_index = _xlsx_col_index(cell.attrib.get("r", "A1"))
            while len(values) <= cell_index:
                values.append("")

            value = ""
            cell_type = cell.attrib.get("t", "")
            if cell_type == "inlineStr":
                value = _xlsx_node_text(cell)
            else:
                value_node = next((child for child in cell if child.tag.endswith("}v")), None)
                raw_value = value_node.text if value_node is not None else ""
                if cell_type == "s" and raw_value:
                    try:
                        value = shared_strings[int(raw_value)]
                    except Exception:
                        value = raw_value
                else:
                    value = raw_value or ""
            values[cell_index] = value.strip()
        if any(values):
            rows.append(values)
    return rows


_CNKI_CACHE: dict[str, Any] = {"path": "", "mtime": 0.0, "rows": []}


def _get_cnki_master_rows() -> list[dict[str, str]]:
    master_path = Path(
        os.getenv(
            "CNKI_MASTER_INDEX_PATH",
            get_local_docs_root() / "CNKI_Merged_Master_Database_finally.xlsx",
        )
    )
    if not master_path.exists():
        return []

    mtime = master_path.stat().st_mtime
    if _CNKI_CACHE["path"] == str(master_path) and _CNKI_CACHE["mtime"] == mtime:
        return _CNKI_CACHE["rows"]

    rows = _read_first_xlsx_sheet(master_path)
    records: list[dict[str, str]] = []
    for row in rows[1:]:
        title = row[0].strip() if len(row) > 0 else ""
        if not title:
            continue
        record = {
            "title": title,
            "author": row[1].strip() if len(row) > 1 else "",
            "abstract": (row[2].strip() if len(row) > 2 else "")[:800],
            "source_library": row[3].strip() if len(row) > 3 else "",
            "organ": row[4].strip() if len(row) > 4 else "",
            "journal": row[5].strip() if len(row) > 5 else "",
            "keywords": row[6].strip() if len(row) > 6 else "",
            "published_at": row[7].strip() if len(row) > 7 else "",
            "url": row[8].strip() if len(row) > 8 else "",
            "norm_title": _normalize_lookup_text(title),
        }
        records.append(record)

    _CNKI_CACHE.update({"path": str(master_path), "mtime": mtime, "rows": records})
    return records


def _find_cnki_metadata(path: Path) -> dict[str, str] | None:
    rows = _get_cnki_master_rows()
    if not rows:
        return None

    by_title = {row["norm_title"]: row for row in rows if row.get("norm_title")}
    candidate_keys = [
        _normalize_lookup_text(candidate)
        for candidate in _title_candidates_from_filename(path)
    ]
    candidate_keys = [key for key in candidate_keys if key]

    for key in candidate_keys:
        if key in by_title:
            return by_title[key]

    best: dict[str, str] | None = None
    best_score = 0.0
    for key in candidate_keys:
        if len(key) < 8:
            continue
        for row in rows:
            title_key = row.get("norm_title", "")
            if not title_key:
                continue
            short, long = (key, title_key) if len(key) <= len(title_key) else (title_key, key)
            if len(short) >= 8 and short in long:
                score = len(short) / max(len(long), 1)
                if score > best_score:
                    best = row
                    best_score = score
    return best if best and best_score >= 0.45 else None


def _header_index(headers: list[str], *needles: str) -> int | None:
    lowered_needles = [needle.lower() for needle in needles]
    for index, header in enumerate(headers):
        normalized = str(header or "").strip().lower()
        if any(needle in normalized for needle in lowered_needles):
            return index
    return None


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    value = str(row[index] or "").strip()
    return "" if value.lower() == "nan" else value


def _safe_virtual_patent_filename(title: str, row_number: int) -> str:
    cleaned = _safe_filename(title or f"patent_{row_number}")
    stem = Path(cleaned).stem[:70].strip(" ._") or f"patent_{row_number}"
    return f"patent_record_{row_number:04d}_{stem}.txt"


def _pool_file_title_candidates(directory: Path) -> list[tuple[str, Path, str]]:
    if not directory.exists():
        return []
    return [
        (_normalize_lookup_text(_stem_without_copy_suffix(path)), path, _stem_without_copy_suffix(path))
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_TEXT_DOCUMENT_EXTENSIONS
    ]


def _find_pool_file_by_title(
    directory: Path,
    title: str,
    candidates_by_stem: list[tuple[str, Path, str]] | None = None,
) -> Path | None:
    title_key = _normalize_lookup_text(title)
    if not title_key:
        return None

    candidates: list[tuple[float, Path]] = []
    pool_candidates = candidates_by_stem if candidates_by_stem is not None else _pool_file_title_candidates(directory)
    for stem_key, path, raw_stem in pool_candidates:
        if not stem_key:
            continue
        if stem_key == title_key:
            return path
        if raw_stem == title:
            return path

    for stem_key, path, raw_stem in pool_candidates:
        if not stem_key:
            continue
        if raw_stem.startswith(title) and (raw_stem == title or raw_stem[len(title) :].startswith(("_", "-", " "))):
            return path
        if len(title_key) >= 4 and stem_key.startswith(title_key):
            candidates.append((0.95, path))
            continue
        short, long = (title_key, stem_key) if len(title_key) <= len(stem_key) else (stem_key, title_key)
        if len(short) >= 8 and short in long:
            candidates.append((len(short) / max(len(long), 1), path))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], len(item[1].stem), item[1].name))
    return candidates[0][1] if candidates[0][0] >= 0.45 else None


def _patent_entries_from_xlsx(table_path: Path, pool_dir: Path) -> list[dict[str, Any]]:
    rows = _read_first_xlsx_sheet(table_path)
    if len(rows) < 2:
        return []

    headers = rows[0]
    source_idx = _header_index(headers, "来源库", "srcdatabase")
    author_idx = _header_index(headers, "作者", "author")
    applicant_idx = _header_index(headers, "申请人", "applicant")
    title_idx = _header_index(headers, "题名", "title")
    summary_idx = _header_index(headers, "摘要", "summary", "abstract")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    entries: list[dict[str, Any]] = []
    pool_candidates = _pool_file_title_candidates(pool_dir)

    for row_number, row in enumerate(rows[1:], start=2):
        title = _cell(row, title_idx)
        if not title:
            continue
        abstract = _cell(row, summary_idx)[:1200] or f"{FILENAME_FALLBACK_PREFIX}{title}"
        author = _cell(row, author_idx)
        applicant = _cell(row, applicant_idx)
        matched_file = _find_pool_file_by_title(pool_dir, title, pool_candidates)
        entry: dict[str, Any] = {
            "title": title,
            "author": author,
            "applicant": applicant,
            "abstract": abstract,
            "file_name": matched_file.name if matched_file else _safe_virtual_patent_filename(title, row_number),
            "source_type": PATENT_SOURCE_TYPE,
            "source_library": _cell(row, source_idx) or "CNKI专利摘要表",
            "source_workbook": table_path.name,
            "source_workbook_path": str(table_path),
            "row_number": row_number,
            "has_source_file": bool(matched_file),
            "source_path": str(matched_file) if matched_file else "",
            "index_method": "cnki_patent_xlsx",
            "updated_at": now,
        }
        entries.append(entry)

    return entries


def rebuild_patents_index_from_pool(
    pool_dir: Path | None = None,
    index_path: Path | None = None,
) -> dict[str, Any]:
    ensure_local_library_dirs()
    target_pool = (pool_dir or get_patent_pool_dir()).resolve()
    target_index = (index_path or get_patents_index_path()).resolve()
    tables = [
        path
        for path in sorted(target_pool.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and path.suffix.lower() in SUPPORTED_PATENT_INDEX_TABLE_EXTENSIONS
    ]

    entries: list[dict[str, Any]] = []
    for table_path in tables:
        entries.extend(_patent_entries_from_xlsx(table_path, target_pool))

    include_unmatched_files = os.getenv("LOCAL_PATENT_INCLUDE_UNMATCHED_FILES", "").strip() == "1" or not tables
    if include_unmatched_files:
        referenced_files = {str(entry.get("file_name", "")).lower() for entry in entries if entry.get("has_source_file")}
        for path in sorted(target_pool.iterdir(), key=lambda item: item.name.lower()):
            if (
                not path.is_file()
                or path.suffix.lower() not in SUPPORTED_TEXT_DOCUMENT_EXTENSIONS
                or path.name.lower() in referenced_files
            ):
                continue
            entries.append(
                {
                    "title": _title_candidates_from_filename(path)[0],
                    "author": _author_from_filename(path),
                    "applicant": "",
                    "abstract": f"{FILENAME_FALLBACK_PREFIX}{path.stem}",
                    "file_name": path.name,
                    "source_type": PATENT_SOURCE_TYPE,
                    "source_library": "专利池文件",
                    "has_source_file": True,
                    "source_path": str(path),
                    "index_method": "filename_patent_file",
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

    _write_json(target_index, entries)
    return {
        "index_path": str(target_index),
        "pool_dir": str(target_pool),
        "table_count": len(tables),
        "entry_count": len(entries),
        "with_source_file_count": sum(1 for item in entries if item.get("has_source_file")),
        "abstract_only_count": sum(1 for item in entries if not item.get("has_source_file")),
    }


def _extract_txt_preview(path: Path, limit: int) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding, errors="ignore")[:limit]
        except Exception:
            continue
    return ""


def _extract_docx_preview(path: Path, limit: int) -> str:
    try:
        with zipfile.ZipFile(path) as docx:
            xml = docx.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        texts = [node.text for node in root.iter() if node.tag.endswith("}t") and node.text]
        return " ".join(texts)[:limit]
    except Exception:
        return ""


def _extract_pdf_preview(path: Path, limit: int) -> str:
    try:
        import fitz  # type: ignore

        chunks: list[str] = []
        with fitz.open(path) as doc:
            for page in doc[: min(3, len(doc))]:
                chunks.append(page.get_text("text"))
                if sum(len(chunk) for chunk in chunks) >= limit:
                    break
        return "\n".join(chunks)[:limit]
    except Exception:
        return ""


def extract_document_preview(path: Path, limit: int = 1200) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        preview = _extract_txt_preview(path, limit)
    elif suffix == ".docx":
        preview = _extract_docx_preview(path, limit)
    elif suffix == ".pdf":
        preview = _extract_pdf_preview(path, limit)
    else:
        preview = ""

    preview = re.sub(r"\s+", " ", preview).strip()
    return preview[:limit] if preview else f"{FILENAME_FALLBACK_PREFIX}{path.stem}"

def _index_entry_for_file(
    file_path: Path,
    source_type: str,
    file_hash: str = "",
) -> dict[str, Any]:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    metadata = _find_cnki_metadata(file_path) if source_type == PAPER_SOURCE_TYPE else None
    preview = ""
    index_method = "filename"

    if metadata:
        preview = metadata.get("abstract", "")
        index_method = "cnki"

    if not preview:
        preview = extract_document_preview(file_path)
        index_method = "pdf_preview" if file_path.suffix.lower() == ".pdf" else "file_preview"

    entry: dict[str, Any] = {
        "title": metadata.get("title") if metadata else _title_candidates_from_filename(file_path)[0],
        "author": (metadata.get("author") if metadata else "") or _author_from_filename(file_path),
        "abstract": preview,
        "file_name": file_path.name,
        "source_type": source_type,
        "size": file_path.stat().st_size,
        "sha256": file_hash or _file_sha256(file_path),
        "index_method": index_method,
        "updated_at": now,
    }

    if metadata:
        for key in ("source_library", "organ", "journal", "keywords", "published_at", "url"):
            if metadata.get(key):
                entry[key] = metadata[key]

    return entry


def _normalize_index_items(items: list[dict[str, Any]], directory: Path) -> list[dict[str, Any]]:
    if not directory.exists():
        return []

    existing_names = {
        path.name.lower()
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_LIBRARY_EXTENSIONS
    }
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if item.get("source_type") == PATENT_SOURCE_TYPE and item.get("index_method") == "cnki_patent_xlsx":
            key = _normalize_lookup_text(item.get("title")) or str(item.get("file_name", "")).lower()
            if key and key not in seen:
                seen.add(key)
                normalized.append(item)
            continue
        lower_name = str(item.get("file_name", "")).lower()
        if not lower_name or lower_name not in existing_names or lower_name in seen:
            continue
        seen.add(lower_name)
        normalized.append(item)
    return normalized


def _upsert_index_entry(
    index_path: Path,
    file_path: Path,
    source_type: str,
    directory: Path,
    file_hash: str = "",
) -> dict[str, Any]:
    items = _normalize_index_items(_read_json_list(index_path), directory)
    entry = _index_entry_for_file(file_path, source_type, file_hash=file_hash)

    lower_name = file_path.name.lower()
    replaced = False
    for index, item in enumerate(items):
        if str(item.get("file_name", "")).lower() == lower_name:
            items[index] = {**item, **entry}
            replaced = True
            break
    if not replaced:
        items.append(entry)

    _write_json(index_path, items)
    return entry

def save_local_library_file(fileobj: BinaryIO, filename: str, target: str) -> dict[str, Any]:
    ensure_local_library_dirs()
    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()

    if target == "all_papers_pool":
        target_dir = get_papers_pool_dir()
        index_path = get_papers_index_path()
        source_type = PAPER_SOURCE_TYPE
    elif target == "user_docs":
        target_dir = get_user_docs_dir()
        index_path = get_user_docs_index_path()
        source_type = USER_DOC_SOURCE_TYPE
    elif target == "all_patent_pool":
        target_dir = get_patent_pool_dir()
        index_path = get_patents_index_path()
        source_type = PATENT_SOURCE_TYPE
    else:
        raise ValueError("target \u53ea\u80fd\u662f user_docs、all_papers_pool \u6216 all_patent_pool")

    if suffix not in SUPPORTED_LIBRARY_EXTENSIONS:
        suffix_label = suffix or "\u65e0\u6269\u5c55\u540d"
        raise ValueError(f"\u4e0d\u652f\u6301\u7684\u6587\u4ef6\u7c7b\u578b\uff1a{suffix_label}")

    target_dir.mkdir(parents=True, exist_ok=True)
    destination = _dedupe_path(target_dir, safe_name)
    file_hash, file_size = _copy_file_with_hash(fileobj, destination)

    duplicate_path = _find_existing_file_by_hash(
        target_dir,
        digest=file_hash,
        size=file_size,
        skip_path=destination,
    )
    if duplicate_path:
        try:
            destination.unlink()
        except OSError:
            pass
        if target == "all_patent_pool" and duplicate_path.suffix.lower() in SUPPORTED_PATENT_INDEX_TABLE_EXTENSIONS:
            rebuild_result = rebuild_patents_index_from_pool(target_dir, index_path)
            return {
                "file_name": duplicate_path.name,
                "target": target,
                "path": str(duplicate_path),
                "index_path": str(index_path),
                "entry": {},
                "duplicate": True,
                "duplicate_of": duplicate_path.name,
                "index_method": "cnki_patent_xlsx",
                **rebuild_result,
            }
        entry = _upsert_index_entry(
            index_path,
            duplicate_path,
            source_type,
            target_dir,
            file_hash=file_hash,
        )
        return {
            "file_name": duplicate_path.name,
            "target": target,
            "path": str(duplicate_path),
            "index_path": str(index_path),
            "entry": entry,
            "duplicate": True,
            "duplicate_of": duplicate_path.name,
            "index_method": entry.get("index_method", ""),
        }

    if target == "all_patent_pool" and destination.suffix.lower() in SUPPORTED_PATENT_INDEX_TABLE_EXTENSIONS:
        rebuild_result = rebuild_patents_index_from_pool(target_dir, index_path)
        return {
            "file_name": destination.name,
            "target": target,
            "path": str(destination),
            "index_path": str(index_path),
            "entry": {},
            "duplicate": False,
            "index_method": "cnki_patent_xlsx",
            **rebuild_result,
        }

    entry = _upsert_index_entry(
        index_path,
        destination,
        source_type,
        target_dir,
        file_hash=file_hash,
    )
    return {
        "file_name": destination.name,
        "target": target,
        "path": str(destination),
        "index_path": str(index_path),
        "entry": entry,
        "duplicate": False,
        "index_method": entry.get("index_method", ""),
    }

def delete_local_library_file(target: str, file_name: str) -> dict[str, Any]:
    ensure_local_library_dirs()
    safe_name = _safe_filename(file_name)
    if target == "all_papers_pool":
        directory = get_papers_pool_dir()
        index_path = get_papers_index_path()
    elif target == "user_docs":
        directory = get_user_docs_dir()
        index_path = get_user_docs_index_path()
    elif target == "all_patent_pool":
        directory = get_patent_pool_dir()
        index_path = get_patents_index_path()
    else:
        raise ValueError("target 只能是 user_docs、all_papers_pool 或 all_patent_pool")

    target_path = (directory / safe_name).resolve()
    if directory.resolve() not in target_path.parents:
        raise ValueError("非法文件路径")

    existed = target_path.exists()
    if existed:
        target_path.unlink()

    if target == "all_patent_pool" and target_path.suffix.lower() in SUPPORTED_PATENT_INDEX_TABLE_EXTENSIONS:
        rebuild_patents_index_from_pool(directory, index_path)
    else:
        items = [
            item
            for item in _read_json_list(index_path)
            if str(item.get("file_name", "")).lower() != safe_name.lower()
        ]
        _write_json(index_path, items)
    return {"deleted": existed, "target": target, "file_name": safe_name}


def resolve_local_library_file(target: str, file_name: str) -> Path:
    ensure_local_library_dirs()
    safe_name = _safe_filename(file_name)
    if target == "all_papers_pool":
        directory = get_papers_pool_dir()
    elif target == "user_docs":
        directory = get_user_docs_dir()
    elif target == "all_patent_pool":
        directory = get_patent_pool_dir()
    else:
        raise ValueError("target 只能是 user_docs、all_papers_pool 或 all_patent_pool")

    target_path = (directory / safe_name).resolve()
    if directory.resolve() not in target_path.parents:
        raise ValueError("非法文件路径")
    if target_path.suffix.lower() not in SUPPORTED_LIBRARY_EXTENSIONS:
        raise ValueError(f"不支持打开的文件类型：{target_path.suffix or '无扩展名'}")
    if not target_path.exists() or not target_path.is_file():
        raise FileNotFoundError(safe_name)
    return target_path


def _count_supported_files(directory: Path, extensions: set[str] | None = None) -> int:
    if not directory.exists():
        return 0
    allowed_extensions = extensions or SUPPORTED_LIBRARY_EXTENSIONS
    return sum(
        1
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in allowed_extensions
    )


def _count_patent_records(index_path: Path) -> int:
    return len(_read_json_list(index_path))


def _file_records(
    directory: Path,
    index_path: Path,
    source_type: str,
    limit: int = 5000,
    search: str = "",
    extensions: set[str] | None = None,
) -> list[dict[str, Any]]:
    index_by_name = {
        str(item.get("file_name", "")).lower(): item
        for item in _read_json_list(index_path)
    }
    if not directory.exists():
        return []

    records = []
    keyword = search.strip().lower()
    allowed_extensions = extensions or SUPPORTED_LIBRARY_EXTENSIONS
    for path in sorted(directory.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file() or path.suffix.lower() not in allowed_extensions:
            continue
        indexed = index_by_name.get(path.name.lower(), {})
        if keyword:
            haystack = " ".join(
                [
                    path.name,
                    str(indexed.get("title") or ""),
                    str(indexed.get("abstract") or ""),
                    str(indexed.get("author") or ""),
                    str(indexed.get("applicant") or ""),
                ]
            ).lower()
            if keyword not in haystack:
                continue
        records.append(
            {
                "file_name": path.name,
                "title": indexed.get("title") or path.stem,
                "abstract": indexed.get("abstract") or "",
                "applicant": indexed.get("applicant") or "",
                "type": source_type,
                "size": path.stat().st_size,
                "updated_at": indexed.get("updated_at") or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime)),
                "indexed": bool(indexed),
            }
        )
        if len(records) >= limit:
            break
    return records


def list_local_library(search: str = "", limit: int = 5000) -> dict[str, Any]:
    ensure_local_library_dirs()
    capped_limit = max(1, min(int(limit or 5000), 10000))
    user_docs = _file_records(get_user_docs_dir(), get_user_docs_index_path(), USER_DOC_SOURCE_TYPE, limit=capped_limit, search=search)
    papers = _file_records(get_papers_pool_dir(), get_papers_index_path(), PAPER_SOURCE_TYPE, limit=capped_limit, search=search)
    patents = _file_records(
        get_patent_pool_dir(),
        get_patents_index_path(),
        PATENT_SOURCE_TYPE,
        limit=capped_limit,
        search=search,
        extensions=SUPPORTED_TEXT_DOCUMENT_EXTENSIONS,
    )
    user_index = _read_json_list(get_user_docs_index_path())
    paper_index = _read_json_list(get_papers_index_path())
    patent_index = _read_json_list(get_patents_index_path())
    user_file_count = _count_supported_files(get_user_docs_dir())
    paper_file_count = _count_supported_files(get_papers_pool_dir())
    patent_file_count = _count_supported_files(get_patent_pool_dir(), SUPPORTED_TEXT_DOCUMENT_EXTENSIONS)
    patent_record_count = _count_patent_records(get_patents_index_path())
    pending_user = sum(1 for item in user_docs if not item["indexed"])
    pending_papers = sum(1 for item in papers if not item["indexed"])
    pending_patents = sum(1 for item in patents if not item["indexed"])

    return {
        "root": str(get_local_docs_root()),
        "user_docs": user_docs,
        "papers": papers,
        "patents": patents,
        "stats": {
            "user_docs_count": user_file_count,
            "papers_count": paper_file_count,
            "patents_count": patent_file_count,
            "patent_records_count": patent_record_count,
            "user_index_count": len(user_index),
            "paper_index_count": len(paper_index),
            "patent_index_count": len(patent_index),
            "pending_index_count": (
                max(user_file_count - len(user_index), 0)
                + max(paper_file_count - len(paper_index), 0)
                + pending_patents
            ),
            "index_health": _index_health(
                user_file_count,
                paper_file_count,
                patent_file_count,
                user_index,
                paper_index,
                patent_index,
            ),
        },
    }


def _index_health(
    user_file_count: int,
    paper_file_count: int,
    patent_file_count: int,
    user_index: list[dict[str, Any]],
    paper_index: list[dict[str, Any]],
    patent_index: list[dict[str, Any]],
) -> int:
    local_item_count = user_file_count + paper_file_count + max(patent_file_count, len(patent_index))
    if local_item_count == 0:
        return 100
    indexed_count = (
        min(len(user_index), user_file_count)
        + min(len(paper_index), paper_file_count)
        + min(len(patent_index), max(patent_file_count, len(patent_index)))
    )
    coverage = indexed_count / local_item_count
    all_index_items = user_index + paper_index + patent_index
    abstract_items = [item for item in all_index_items if str(item.get("abstract", "")).strip()]
    abstract_score = min(len(abstract_items) / max(len(all_index_items), 1), 1.0)
    return round((coverage * 0.7 + abstract_score * 0.3) * 100)
