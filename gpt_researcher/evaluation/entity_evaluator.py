from __future__ import annotations

import io
import json
import re
import ssl
import zipfile
from difflib import get_close_matches
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen


ENTITY_THRESHOLD = 0.9
AUTO_EVIDENCE_THRESHOLD = 0.9
URL_RE = re.compile(r"https?://[^\s<>()\[\]{}，。；;、]+")
EVIDENCE_REF_RE = re.compile(r"\[(原文|URL|来源|文献)\s*\d*\]", re.IGNORECASE)
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
ALNUM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-./+]*")
CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
NUMBER_RE = re.compile(
    r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|％|亿美元|万美元|亿元|万元|台|架|小时|个|家|年|月|日)?"
    r"|[12]\d{3}\s*年?"
    r"|q[1-4]"
    r"|第[一二三四]季度"
    r"|[一二三四]季度",
    re.IGNORECASE,
)
EN_MONTHS = {
    "january": "1",
    "jan": "1",
    "february": "2",
    "feb": "2",
    "march": "3",
    "mar": "3",
    "april": "4",
    "apr": "4",
    "may": "5",
    "june": "6",
    "jun": "6",
    "july": "7",
    "jul": "7",
    "august": "8",
    "aug": "8",
    "september": "9",
    "sep": "9",
    "sept": "9",
    "october": "10",
    "oct": "10",
    "november": "11",
    "nov": "11",
    "december": "12",
    "dec": "12",
}
STOP_TERMS = {
    "the",
    "and",
    "for",
    "with",
    "of",
    "in",
    "to",
    "a",
    "an",
    "与",
    "和",
    "及",
    "或",
    "的",
    "为",
    "是",
    "作为",
    "项目",
    "问题",
    "影响",
    "情况",
    "描述",
    "数值",
}
EQUIVALENT_TERM_GROUPS = [
    ["pratt & whitney", "pratt and whitney", "p&w", "pw", "普惠", "普惠公司"],
    ["gtf", "pw1000g", "pw1100g", "pw1500g", "pw1900g", "齿轮传动涡扇", "geared turbofan"],
    ["gtf advantage", "gtfadvantage", "advantage构型", "增强型gtf"],
    ["leap", "leap-1a", "leap1a", "leap-1b", "leap1b", "leap-1c", "leap1c"],
    ["cfm", "cfm international", "cfm国际"],
    ["faa", "美国联邦航空局", "federal aviation administration"],
    ["easa", "欧洲航空安全局", "european union aviation safety agency"],
    ["caac", "中国民航局", "民航局", "civil aviation administration of china"],
    ["airworthiness directive", "ad", "适航指令"],
    ["service bulletin", "sb", "服务通告"],
    ["type certificate", "型号合格证", "tc"],
    ["certification", "适航认证", "认证", "审定", "取证"],
    ["mro", "maintenance repair overhaul", "维修", "维护", "大修", "检修"],
    ["shop visit", "进厂维修", "大修进厂"],
    ["powder metal", "powdered metal", "粉末金属", "粉末冶金"],
    ["contamination", "污染", "杂质"],
    ["fatigue", "疲劳", "早期疲劳", "疲劳失效"],
    ["hot section", "热端", "热端部件"],
    ["maintenance interval", "维护间隔", "维修间隔", "检修间隔"],
    ["durability", "耐久性", "耐用性"],
    ["saf", "可持续航空燃料", "sustainable aviation fuel"],
    ["co2", "co₂", "二氧化碳"],
    ["nox", "氮氧化物"],
    ["a320neo", "a320 neo", "空客a320neo"],
    ["a220", "空客a220"],
    ["e2", "巴航e2", "embraer e2"],
    ["ameco", "北京飞机维修工程有限公司", "北京飞机维修"],
    ["enginewise", "按小时付费包修", "包修模式"],
    ["additive manufacturing", "增材制造", "3d打印"],
]


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_ground_truth_dir() -> Path:
    directory = get_project_root() / "outputs" / "records" / "entity_ground_truths"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _safe_name(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\n\r]+', "", text or "").strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:80] or "default"


def _normalize_entity(text: Any) -> str:
    value = str(text or "").lower()
    value = re.sub(r"[\s\-_./:：,，;；()（）\[\]【】'\"“”‘’]+", "", value)
    return value


def _normalize_match_text(text: Any) -> str:
    value = str(text or "").lower()
    replacements = {
        "％": "%",
        "－": "-",
        "–": "-",
        "—": "-",
        "‑": "-",
        "−": "-",
        "～": "-",
        "~": "-",
        "至": "-",
        "到": "-",
        "co₂": "co2",
        "co2": "co2",
        "pratt＆whitney": "pratt&whitney",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = re.sub(r"(?<=\d),(?=\d{3}\b)", "", value)
    value = re.sub(r"[\s\-_./:：,，;；()（）\[\]【】'\"“”‘’&]+", "", value)
    return value


def _dedupe_terms(terms: Iterable[str], max_terms: Optional[int] = None) -> List[str]:
    seen = set()
    unique_terms = []
    for term in terms:
        cleaned = str(term or "").strip()
        key = _normalize_match_text(cleaned)
        if not key or key in seen or key in STOP_TERMS:
            continue
        seen.add(key)
        unique_terms.append(cleaned)
        if max_terms and len(unique_terms) >= max_terms:
            break
    return unique_terms


def _synonym_variants(term: str) -> List[str]:
    normalized = _normalize_match_text(term)
    variants = [term]
    for group in EQUIVALENT_TERM_GROUPS:
        normalized_group = {_normalize_match_text(item) for item in group}
        if normalized in normalized_group or any(item and item in normalized for item in normalized_group):
            variants.extend(group)
    return _dedupe_terms(variants)


def _cjk_phrase_terms(block: str) -> List[str]:
    terms = [block]
    if len(block) >= 5:
        for size in (6, 5, 4):
            if len(block) >= size:
                for index in range(0, len(block) - size + 1, max(2, size - 2)):
                    terms.append(block[index : index + size])

    preferred_fragments = [
        "粉末金属",
        "粉末冶金",
        "热端部件",
        "维护间隔",
        "维修间隔",
        "适航认证",
        "型号合格证",
        "持续适航",
        "服务通告",
        "适航指令",
        "增材制造",
        "修复时间",
        "可修复",
        "发动机召回",
        "停飞",
        "市场份额",
        "维修市场",
        "维护成本",
        "生命周期成本",
        "燃油消耗",
        "飞行测试",
        "耐久性试验",
        "唯一动力",
        "中国首家",
        "大修中心",
    ]
    terms.extend(fragment for fragment in preferred_fragments if fragment in block)
    return _dedupe_terms(terms)


def _normalize_file_key(value: Any) -> str:
    text = unquote(str(value or "")).lower()
    text = Path(text).stem if "." in Path(text).name else text
    text = text.replace("％", "%")
    text = re.sub(r"[\s\-_./:：,，;；()（）\[\]【】'\"“”‘’]+", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff%]+", "", text, flags=re.UNICODE)
    return text


def _local_source_lookup_dirs() -> List[Path]:
    root = get_project_root()
    return [
        root / "local_docs" / "all_papers_pool",
        root / "local_docs" / "user_docs",
        root / "temp_docs",
        root / "my-docs",
    ]


@lru_cache(maxsize=1)
def _local_source_file_index() -> Dict[str, str]:
    file_index: Dict[str, str] = {}
    supported_suffixes = {".pdf", ".doc", ".docx", ".txt", ".md"}
    for directory in _local_source_lookup_dirs():
        if not directory.exists() or not directory.is_dir():
            continue
        try:
            iterator = directory.rglob("*") if directory.name == "temp_docs" else directory.iterdir()
            for path in iterator:
                if not path.is_file() or path.suffix.lower() not in supported_suffixes:
                    continue
                for key_source in (path.name, path.stem):
                    key = _normalize_file_key(key_source)
                    if key and key not in file_index:
                        file_index[key] = str(path)
        except Exception:
            continue
    return file_index


def _fuzzy_local_source_candidates(file_name: str) -> List[Path]:
    key = _normalize_file_key(file_name)
    if not key:
        return []

    file_index = _local_source_file_index()
    if key in file_index:
        return [Path(file_index[key])]

    contains_matches = [
        Path(path)
        for indexed_key, path in file_index.items()
        if key in indexed_key or indexed_key in key
    ]
    if contains_matches:
        return sorted(contains_matches, key=lambda path: (len(path.stem), path.name))[:5]

    close_keys = get_close_matches(key, list(file_index.keys()), n=5, cutoff=0.86)
    return [Path(file_index[close_key]) for close_key in close_keys]


def _split_markdown_row(row: str) -> List[str]:
    row = row.strip()
    if not row.startswith("|") or not row.endswith("|"):
        return []
    cells = []
    current = []
    escaped = False
    for char in row[1:-1]:
        if char == "\\" and not escaped:
            escaped = True
            current.append(char)
            continue
        if char == "|" and not escaped:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        escaped = False
    cells.append("".join(current).strip())
    return [cell.replace("\\|", "|").strip() for cell in cells]


def _is_table_separator(cells: Iterable[str]) -> bool:
    return all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell or "") for cell in cells)


def _heading_block(report: str, heading_keywords: Iterable[str]) -> str:
    lines = report.splitlines()
    start: Optional[int] = None
    start_level: Optional[int] = None
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+", line)
        if match and all(keyword in line for keyword in heading_keywords):
            start = index + 1
            start_level = len(match.group(1))
            break
    if start is None:
        return ""

    block_lines = []
    for line in lines[start:]:
        match = re.match(r"^(#{1,6})\s+", line)
        if match and start_level is not None and len(match.group(1)) <= start_level:
            break
        block_lines.append(line)
    return "\n".join(block_lines)


def _pick_column(headers: List[str], candidates: Iterable[str], default: Optional[int] = None) -> Optional[int]:
    for candidate in candidates:
        for index, header in enumerate(headers):
            if candidate in header:
                return index
    return default


def _is_entity_table_header(cells: List[str]) -> bool:
    joined = " ".join(cells)
    return "实体" in joined and "参数" in joined


def _extract_evidence_map(report: str) -> Dict[str, str]:
    evidence_map: Dict[str, str] = {}
    for line in report.splitlines():
        stripped = line.strip().lstrip("-*").strip()
        match = re.match(r"(\[(?:原文|URL|来源|文献)\s*\d*\])\s*(.+)", stripped, flags=re.IGNORECASE)
        if match:
            evidence_map[match.group(1)] = match.group(2).strip()
            continue
        cells = _split_markdown_row(stripped)
        if len(cells) >= 2 and EVIDENCE_REF_RE.fullmatch(cells[0].strip()):
            evidence_map[cells[0].strip()] = cells[1].strip()
    return evidence_map


def _candidate_source_paths(file_name: str) -> List[Path]:
    root = get_project_root()
    name = unquote(file_name or "").strip().strip('"')
    if not name:
        return []

    direct = Path(name)
    candidates = []
    if direct.is_absolute():
        candidates.append(direct)
    candidates.extend(
        [
            root / name,
            root / "local_docs" / "all_papers_pool" / name,
            root / "local_docs" / "user_docs" / name,
        ]
    )

    existing = [path for path in candidates if path.exists() and path.is_file()]
    if existing:
        return existing + [path for path in candidates if path not in existing]

    fuzzy_candidates = _fuzzy_local_source_candidates(name)
    return fuzzy_candidates + candidates


def _extract_pdf_text_from_bytes(raw: bytes, limit: int) -> str:
    if not raw:
        return ""
    try:
        import fitz  # type: ignore

        chunks: List[str] = []
        with fitz.open(stream=raw, filetype="pdf") as doc:
            max_pages = min(40, len(doc))
            for page in doc[:max_pages]:
                chunks.append(page.get_text("text", sort=True))
                if sum(len(chunk) for chunk in chunks) >= limit:
                    break
        return "\n".join(chunks)[:limit]
    except Exception:
        return ""


def _extract_docx_text(path: Path, limit: int) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            chunks = []
            for name in archive.namelist():
                if name.startswith("word/") and name.endswith(".xml"):
                    xml = archive.read(name).decode("utf-8", errors="ignore")
                    xml = re.sub(r"<[^>]+>", " ", xml)
                    chunks.append(unescape(xml))
                    if sum(len(chunk) for chunk in chunks) >= limit:
                        break
            return re.sub(r"\s+", " ", " ".join(chunks)).strip()[:limit]
    except Exception:
        return ""


@lru_cache(maxsize=64)
def _read_local_source_text(source: str, limit: int = 160000) -> str:
    for path in _candidate_source_paths(source):
        if not path.exists() or not path.is_file():
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".pdf":
                import fitz  # type: ignore

                chunks: List[str] = []
                with fitz.open(path) as doc:
                    max_pages = min(40, len(doc))
                    for page in doc[:max_pages]:
                        chunks.append(page.get_text("text", sort=True))
                        if sum(len(chunk) for chunk in chunks) >= limit:
                            break
                return "\n".join(chunks)[:limit]
            if suffix == ".docx":
                return _extract_docx_text(path, limit)
            if suffix in {".txt", ".md"}:
                for encoding in ("utf-8", "utf-8-sig", "gb18030"):
                    try:
                        return path.read_text(encoding=encoding, errors="ignore")[:limit]
                    except Exception:
                        continue
        except Exception:
            return ""
    return ""


def _decode_response_text(raw: bytes, charset: Optional[str]) -> str:
    for encoding in [charset, "utf-8", "utf-8-sig", "gb18030"]:
        if not encoding:
            continue
        try:
            return raw.decode(encoding, errors="ignore")
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


def _html_to_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<!--[\s\S]*?-->", " ", text)
    text = re.sub(r"<(br|p|div|li|tr|h[1-6])\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


@lru_cache(maxsize=64)
def _read_url_text(url: str, limit: int = 80000) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 Intelligence-System-Entity-Check",
        "Accept": "text/html,application/pdf,application/xhtml+xml,*/*",
    }
    attempts = [None]
    if url.lower().startswith("https://"):
        attempts.append(ssl._create_unverified_context())

    for context in attempts:
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=12, context=context) as response:
                content_type = response.headers.get("Content-Type", "")
                charset = response.headers.get_content_charset()
                likely_pdf = "application/pdf" in content_type.lower() or url.split("?")[0].lower().endswith(".pdf")
                raw_limit = 8 * 1024 * 1024 if likely_pdf else max(limit * 3, limit)
                raw = response.read(raw_limit)

            if likely_pdf:
                pdf_text = _extract_pdf_text_from_bytes(raw, limit)
                if pdf_text:
                    return pdf_text

            text = _decode_response_text(raw, charset)
            return _html_to_text(text)[:limit]
        except (HTTPError, URLError, TimeoutError, ssl.SSLError, Exception):
            continue
    return ""


def _source_text_for_evidence_ref(ref: str, evidence_map: Dict[str, str]) -> tuple[str, str, str]:
    mapped = evidence_map.get(ref, "").strip()
    if not mapped:
        return "", "missing_source_mapping", ""

    url_match = URL_RE.search(mapped)
    if url_match:
        text = _read_url_text(url_match.group(0))
        return text, "url" if text else "url_unreadable", mapped

    file_match = re.search(r"([^|：:\n\r]+?\.(?:pdf|docx?|txt|md))", mapped, flags=re.IGNORECASE)
    if file_match:
        source_name = file_match.group(1).strip()
        text = _read_local_source_text(source_name)
        return text, "local_file" if text else "local_file_unreadable", mapped

    return "", "unsupported_source_type", mapped


def _important_terms(text: str, max_terms: int = 12) -> List[str]:
    terms: List[str] = []
    for token in ALNUM_RE.findall(text or ""):
        cleaned = token.strip("._-/+").lower()
        if len(cleaned) >= 2 and cleaned not in STOP_TERMS:
            terms.append(cleaned)
            terms.extend(_synonym_variants(cleaned))
    for block in CJK_RE.findall(text or ""):
        terms.extend(_cjk_phrase_terms(block))
        terms.extend(_synonym_variants(block))
    return _dedupe_terms(terms, max_terms=max_terms)


def _numbers_in_text(text: str) -> List[str]:
    variants: List[str] = []
    for match in NUMBER_RE.findall(text or ""):
        token = str(match or "").strip()
        if not token:
            continue
        normalized = _normalize_match_text(token)
        variants.extend([token, normalized])

        without_commas = token.replace(",", "")
        variants.append(without_commas)
        if "%" in token or "％" in token:
            number = re.sub(r"[^0-9.]", "", token)
            if number:
                variants.extend([f"{number}%", f"{number}％", number])
        if re.fullmatch(r"q[1-4]", token, flags=re.IGNORECASE):
            quarter = token[-1]
            cn_quarters = {"1": "第一季度", "2": "第二季度", "3": "第三季度", "4": "第四季度"}
            variants.append(cn_quarters.get(quarter, token))
        quarter_match = re.fullmatch(r"(?:第)?([一二三四])季度", token)
        if quarter_match:
            quarter_map = {"一": "Q1", "二": "Q2", "三": "Q3", "四": "Q4"}
            variants.append(quarter_map[quarter_match.group(1)])
        year_match = re.fullmatch(r"([12]\d{3})年?", token)
        if year_match:
            variants.extend([year_match.group(1), f"{year_match.group(1)}年"])
    for match in re.finditer(
        r"\b("
        + "|".join(re.escape(month) for month in EN_MONTHS)
        + r")\s+(\d{1,2},\s*)?([12]\d{3})\b",
        text or "",
        flags=re.IGNORECASE,
    ):
        month = EN_MONTHS[match.group(1).lower()]
        day = re.sub(r"\D", "", match.group(2) or "")
        year = match.group(3)
        variants.extend([f"{year}年{month}月", f"{year}-{int(month):02d}"])
        if day:
            variants.extend([f"{year}年{month}月{int(day)}日", f"{year}-{int(month):02d}-{int(day):02d}"])
    for match in re.finditer(
        r"\b([12]\d{3})\s+("
        + "|".join(re.escape(month) for month in EN_MONTHS)
        + r")\s+(\d{1,2})?\b",
        text or "",
        flags=re.IGNORECASE,
    ):
        year = match.group(1)
        month = EN_MONTHS[match.group(2).lower()]
        day = match.group(3)
        variants.extend([f"{year}年{month}月", f"{year}-{int(month):02d}"])
        if day:
            variants.extend([f"{year}年{month}月{int(day)}日", f"{year}-{int(month):02d}-{int(day):02d}"])
    return _dedupe_terms(variants)


def _term_hit_count(terms: Iterable[str], source_text: str) -> int:
    normalized_source = _normalize_match_text(source_text)
    count = 0
    for term in terms:
        if _normalize_match_text(term) in normalized_source:
            count += 1
    return count


def _check_entity_against_source(entity: Dict[str, Any], source_text: str) -> tuple[str, str, float]:
    if not source_text:
        return "unchecked", "证据源无法读取，未能自动核验。", 0.0

    name = str(entity.get("name") or "")
    value = str(entity.get("value") or "")
    name_terms = _important_terms(name, max_terms=6)
    value_terms = _important_terms(value, max_terms=10)
    value_numbers = _numbers_in_text(value)

    name_hits = _term_hit_count(name_terms, source_text)
    value_hits = _term_hit_count(value_terms, source_text)
    number_hits = _term_hit_count(value_numbers, source_text)

    name_score = name_hits / max(len(name_terms), 1)
    value_signal_total = max(len(value_terms) + len(value_numbers), 1)
    value_score = (value_hits + number_hits) / value_signal_total
    confidence = round(min(1.0, 0.65 * name_score + 0.35 * value_score), 4)

    if name_hits and (value_hits or number_hits or not value.strip() or value.strip() in {"-", "—"}):
        return "supported", "证据中命中实体名称，并命中关键描述或数值。", confidence
    if name_hits or value_hits >= 2 or number_hits >= 2:
        return "partially_supported", "证据中命中部分实体名称、描述或数值，但支撑不完整。", confidence
    return "unsupported", "已读取证据源，但未命中实体名称或关键描述。", confidence


def _auto_check_entity_evidence(entity: Dict[str, Any], evidence_map: Dict[str, str]) -> Dict[str, Any]:
    refs = [match.group(0) for match in re.finditer(EVIDENCE_REF_RE, str(entity.get("evidence") or ""))]
    if not refs:
        direct_url = URL_RE.search(str(entity.get("evidence") or ""))
        if direct_url:
            refs = [direct_url.group(0)]
        else:
            return {
                "status": "unchecked",
                "confidence": 0.0,
                "checked_refs": [],
                "reason": "实体没有可解析的证据编号或真实 URL。",
            }

    checks = []
    for ref in refs[:3]:
        if ref.startswith("http"):
            source_text = _read_url_text(ref)
            source_type = "url" if source_text else "url_unreadable"
            mapped_source = ref
        else:
            source_text, source_type, mapped_source = _source_text_for_evidence_ref(ref, evidence_map)
        status, reason, confidence = _check_entity_against_source(entity, source_text)
        checks.append(
            {
                "ref": ref,
                "source": mapped_source,
                "source_type": source_type,
                "status": status,
                "confidence": confidence,
                "reason": reason,
            }
        )

    status_rank = {"supported": 4, "partially_supported": 3, "unsupported": 2, "unchecked": 1}
    best = max(checks, key=lambda item: (status_rank.get(item["status"], 0), item.get("confidence", 0.0)))
    return {
        "status": best["status"],
        "confidence": best["confidence"],
        "checked_refs": checks,
        "reason": best["reason"],
    }


def _run_auto_evidence_check(entities: List[Dict[str, Any]], report: str) -> Dict[str, Any]:
    evidence_map = _extract_evidence_map(report)
    supported = partial = unsupported = unchecked = 0

    for entity in entities:
        check = _auto_check_entity_evidence(entity, evidence_map)
        entity["auto_evidence_check"] = check
        status = check.get("status")
        if status == "supported":
            supported += 1
        elif status == "partially_supported":
            partial += 1
        elif status == "unsupported":
            unsupported += 1
        else:
            unchecked += 1

    checked_total = supported + partial + unsupported
    weighted_score = supported + 0.5 * partial
    auto_accuracy = round(weighted_score / checked_total, 4) if checked_total else None
    strict_accuracy = round(weighted_score / len(entities), 4) if entities else None

    return {
        "method": "source_text_alias_number_url_pdf_matching",
        "supported_count": supported,
        "partially_supported_count": partial,
        "unsupported_count": unsupported,
        "unchecked_count": unchecked,
        "checked_count": checked_total,
        "auto_evidence_accuracy": auto_accuracy,
        "strict_auto_evidence_accuracy": strict_accuracy,
        "threshold": AUTO_EVIDENCE_THRESHOLD,
        "requirement_met": auto_accuracy is not None and auto_accuracy >= AUTO_EVIDENCE_THRESHOLD,
        "note": "该分数表示已抽取实体是否能被证据文本支撑，不等同于含遗漏率的金标准实体抽取准确率。",
    }


def _selected_file_names(selected_sources: Iterable[Any]) -> List[str]:
    names = []
    for item in selected_sources or []:
        name = getattr(item, "file_name", None)
        if name is None and isinstance(item, dict):
            name = item.get("file_name")
        if name:
            names.append(str(name))
    return names


def _has_real_evidence(evidence: str, evidence_map: Dict[str, str], selected_files: List[str]) -> bool:
    text = evidence or ""
    if URL_RE.search(text):
        return True

    refs = EVIDENCE_REF_RE.findall(text)
    if refs:
        for ref_match in re.finditer(EVIDENCE_REF_RE, text):
            mapped = evidence_map.get(ref_match.group(0), "")
            if URL_RE.search(mapped):
                return True
            if re.search(r"\.(pdf|docx?|txt|md)\b", mapped, flags=re.IGNORECASE):
                return True
            if any(file_name and file_name in mapped for file_name in selected_files):
                return True
        return False

    return any(file_name and file_name in text for file_name in selected_files)


def extract_entities_from_report(report: str, selected_sources: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    block = _heading_block(report or "", ("实体", "参数"))
    if not block:
        return []

    table_rows: List[List[str]] = []
    collecting_entity_table = False
    for line in block.splitlines():
        if "证据来源" in line or "来源列表" in line:
            break
        if collecting_entity_table and MARKDOWN_HEADING_RE.match(line):
            break

        cells = _split_markdown_row(line)
        if not cells:
            continue

        if _is_entity_table_header(cells):
            if table_rows:
                break
            table_rows.append(cells)
            collecting_entity_table = True
            continue

        if collecting_entity_table:
            table_rows.append(cells)

    if len(table_rows) < 2:
        return []

    headers = table_rows[0]
    type_col = _pick_column(headers, ("类别", "类型"), default=None)
    name_col = _pick_column(headers, ("实体/参数", "实体", "参数", "名称"), default=0)
    value_col = _pick_column(headers, ("数值/描述", "取值", "描述", "内容"), default=1 if len(headers) > 1 else None)
    evidence_col = _pick_column(headers, ("证据", "依据", "来源"), default=len(headers) - 1 if len(headers) > 2 else None)
    confidence_col = _pick_column(headers, ("可信度", "置信度", "信度"), default=None)

    evidence_map = _extract_evidence_map(report)
    selected_files = _selected_file_names(selected_sources)
    entities: List[Dict[str, Any]] = []
    seen = set()

    for row in table_rows[1:]:
        if _is_table_separator(row):
            continue
        if not row or len(row) < 2:
            continue

        def cell(index: Optional[int]) -> str:
            if index is None or index >= len(row):
                return ""
            return row[index].strip()

        name = cell(name_col)
        value = cell(value_col)
        entity_type = cell(type_col) or "未分类"
        evidence = cell(evidence_col)
        confidence = cell(confidence_col) or ""
        if not name or name in {"-", "无", "待人工整理"}:
            continue

        key = (_normalize_entity(entity_type), _normalize_entity(name), _normalize_entity(value))
        if key in seen:
            continue
        seen.add(key)

        has_evidence = _has_real_evidence(evidence, evidence_map, selected_files)
        entities.append(
            {
                "type": entity_type,
                "name": name,
                "value": value,
                "evidence": evidence,
                "evidence_supported": has_evidence,
                "confidence": confidence or ("high" if has_evidence else "pending"),
                "review_status": "unreviewed",
            }
        )

    return entities


def _ground_truth_candidates(task: str) -> List[Path]:
    directory = get_ground_truth_dir()
    safe_task = _safe_name(task)
    return [
        directory / f"{safe_task}.json",
        directory / f"{safe_task[:30]}.json",
        directory / "ground_truth.json",
    ]


def _load_ground_truth(task: str) -> tuple[Optional[Path], List[Dict[str, Any]]]:
    for path in _ground_truth_candidates(task):
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            return path, [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            entities = data.get("entities") or data.get("expected_entities") or []
            if isinstance(entities, list):
                return path, [item for item in entities if isinstance(item, dict)]
    return None, []


def _entity_name(item: Dict[str, Any]) -> str:
    return str(item.get("name") or item.get("entity") or item.get("实体") or item.get("实体/参数") or "").strip()


def _match_entities(extracted: List[Dict[str, Any]], expected: List[Dict[str, Any]]) -> Dict[str, Any]:
    extracted_norm = [(_normalize_entity(_entity_name(item)), item) for item in extracted]
    expected_norm = [(_normalize_entity(_entity_name(item)), item) for item in expected]
    matched_extracted = set()
    matched_expected = set()

    for expected_index, (expected_name, _expected_item) in enumerate(expected_norm):
        if not expected_name:
            continue
        for extracted_index, (extracted_name, _extracted_item) in enumerate(extracted_norm):
            if extracted_index in matched_extracted or not extracted_name:
                continue
            if expected_name == extracted_name or expected_name in extracted_name or extracted_name in expected_name:
                matched_expected.add(expected_index)
                matched_extracted.add(extracted_index)
                break

    correct = [extracted[index] for index in sorted(matched_extracted)]
    wrong = [item for index, item in enumerate(extracted) if index not in matched_extracted]
    missed = [item for index, item in enumerate(expected) if index not in matched_expected]
    denominator = len(correct) + len(wrong) + len(missed)
    accuracy = round(len(correct) / denominator, 4) if denominator else None
    precision_denominator = len(correct) + len(wrong)
    accuracy_without_missed = round(len(correct) / precision_denominator, 4) if precision_denominator else None

    return {
        "correct_entities": correct,
        "wrong_entities": wrong,
        "missed_entities": missed,
        "accuracy": accuracy,
        "accuracy_without_missed": accuracy_without_missed,
    }


def evaluate_report_entities(
    report: str,
    task: str,
    selected_sources: Iterable[Any] = (),
    threshold: float = ENTITY_THRESHOLD,
) -> Dict[str, Any]:
    extracted = extract_entities_from_report(report, selected_sources)
    auto_evidence_eval = _run_auto_evidence_check(extracted, report) if extracted else {
        "method": "source_text_alias_number_url_pdf_matching",
        "supported_count": 0,
        "partially_supported_count": 0,
        "unsupported_count": 0,
        "unchecked_count": 0,
        "checked_count": 0,
        "auto_evidence_accuracy": None,
        "strict_auto_evidence_accuracy": None,
        "threshold": AUTO_EVIDENCE_THRESHOLD,
        "requirement_met": None,
        "note": "未抽取到实体，未执行自动证据核验。",
    }
    ground_truth_path, expected = _load_ground_truth(task)
    evidence_supported_count = sum(1 for item in extracted if item.get("evidence_supported"))
    unsupported_count = len(extracted) - evidence_supported_count

    base: Dict[str, Any] = {
        "status": "auto_evidence_checked" if extracted else "pending_manual_review",
        "method": "report_entity_table_extraction",
        "threshold": threshold,
        "ground_truth_path": str(ground_truth_path) if ground_truth_path else "",
        "extracted_count": len(extracted),
        "evidence_supported_count": evidence_supported_count,
        "unsupported_count": unsupported_count,
        "auto_evidence_eval": auto_evidence_eval,
        "extracted_entities": extracted,
        "expected_entities": None,
        "correct_entities": None,
        "wrong_entities": None,
        "missed_entities": None,
        "accuracy": None,
        "accuracy_without_missed": auto_evidence_eval.get("auto_evidence_accuracy"),
        "accuracy_without_missed_method": "auto_evidence_check",
        "accuracy_without_missed_requirement_met": auto_evidence_eval.get("requirement_met"),
        "requirement_met": None,
        "note": "未提供标准答案，已完成自动证据核验；金标准实体准确率仍需标准答案或人工复核。",
    }

    if not extracted:
        base["status"] = "no_entity_table_found"
        base["note"] = "未从报告的“实体与参数清单”表格中抽取到实体；请检查 Writer Agent 是否按 V1.2 格式输出。"
        return base

    if not expected:
        return base

    matched = _match_entities(extracted, expected)
    accuracy = matched["accuracy"]
    base.update(
        {
            "status": "auto_evaluated",
            "expected_entities": expected,
            "correct_entities": matched["correct_entities"],
            "wrong_entities": matched["wrong_entities"],
            "missed_entities": matched["missed_entities"],
            "accuracy": accuracy,
            "accuracy_without_missed": matched["accuracy_without_missed"],
            "accuracy_without_missed_method": "ground_truth_precision",
            "accuracy_without_missed_requirement_met": (
                matched["accuracy_without_missed"] is not None
                and matched["accuracy_without_missed"] >= threshold
            ),
            "requirement_met": accuracy is not None and accuracy >= threshold,
            "note": "已根据标准答案文件自动计算实体抽取准确率。",
        }
    )
    return base
