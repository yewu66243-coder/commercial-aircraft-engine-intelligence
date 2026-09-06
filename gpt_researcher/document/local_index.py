from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from gpt_researcher.document.local_library import (
    get_patent_pool_dir,
    get_patents_index_path,
    get_user_docs_dir,
    get_user_docs_index_path,
)


SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".doc", ".docx", ".md"}
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-./+]*")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
FILENAME_CLEAN_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
MODEL_ANCHOR_ALIASES = {
    "gtf": ["gtf", "pw1000g", "pw1100g", "pw1500g", "pw1900g", "普惠公司", "普惠发动机", "pratt", "whitney", "齿轮传动涡扇", "geared turbofan"],
    "leap": ["leap", "leap-1a", "leap-1b", "leap-1c", "cfm", "cfm国际", "赛峰", "safran"],
    "cfm56": ["cfm56", "cfm-56", "cfm国际", "赛峰", "safran"],
}
MODEL_PRIMARY_ANCHORS = {
    "gtf": ["gtf", "pw1000g", "pw1100g", "pw1500g", "pw1900g", "齿轮传动涡扇", "geared turbofan"],
    "leap": ["leap", "leap-1a", "leap-1b", "leap-1c"],
    "cfm56": ["cfm56", "cfm-56"],
}
MODEL_NEGATIVE_CONTEXT = [
    "固体火箭",
    "固冲",
    "冲压发动机",
    "火箭冲压",
    "空空导弹",
    "燃气流量可调固体",
]
MODEL_DOMAIN_CONTEXT = [
    "航空发动机",
    "民用航空动力",
    "商用发动机",
    "涡扇发动机",
    "窄体干线客机动力",
    "适航",
    "mro",
    "维修",
    "高压涡轮",
    "发动机制造商",
]
ENGINE_DOMAIN_TERMS = [
    "航空发动机",
    "飞机发动机",
    "民用航空动力",
    "商用航空发动机",
    "商用发动机",
    "涡扇发动机",
    "涡轮风扇发动机",
    "燃气涡轮发动机",
    "发动机制造商",
    "发动机维修",
    "发动机短缺",
    "发动机市场",
    "发动机mro",
    "aero engine",
    "aircraft engine",
    "civil aero engine",
    "commercial engine",
    "turbofan",
    "gas turbine",
    "propulsion",
    "gtf",
    "leap",
    "cfm56",
    "pw1000g",
    "pw1100g",
    "pw1500g",
    "pw1900g",
]
ENGINE_QUERY_TERMS = [
    "航空发动机",
    "飞机发动机",
    "民用航空动力",
    "商用航空发动机",
    "涡扇",
    "发动机",
    "动力装置",
    "aero engine",
    "aircraft engine",
    "turbofan",
    "propulsion",
    "gtf",
    "leap",
    "cfm56",
]
TOPIC_PROFILES = {
    "airworthiness": {
        "query": [
            "适航",
            "审定",
            "取证",
            "认证",
            "适航指令",
            "服务通告",
            "规章",
            "faa",
            "easa",
            "caac",
            "airworthiness",
            "directive",
            "certification",
        ],
        "document": [
            "适航",
            "审定",
            "取证",
            "认证",
            "适航指令",
            "服务通告",
            "安全通告",
            "规章",
            "ccar",
            "faa",
            "easa",
            "caac",
            "ad",
            "sb",
            "airworthiness",
            "airworthiness directive",
            "service bulletin",
            "certification",
        ],
    },
    "maintenance": {
        "query": [
            "mro",
            "维修",
            "维护",
            "大修",
            "检修",
            "售后",
            "保障",
            "运维",
            "在翼",
            "maintenance",
            "overhaul",
        ],
        "document": [
            "mro",
            "维修",
            "维护",
            "大修",
            "检修",
            "售后服务",
            "维修厂",
            "航线维修",
            "预测性维修",
            "在翼",
            "服役",
            "机队",
            "备发",
            "maintenance",
            "overhaul",
            "shop visit",
        ],
    },
    "market": {
        "query": [
            "市场",
            "影响",
            "竞争",
            "趋势",
            "市场需求",
            "维修需求",
            "售后需求",
            "订单",
            "交付",
            "份额",
            "短缺",
            "供应链",
            "预测",
            "商业",
            "market",
        ],
        "document": [
            "市场",
            "mro市场",
            "维修市场",
            "售后服务市场",
            "市场需求",
            "维修需求",
            "售后需求",
            "订单",
            "交付",
            "份额",
            "短缺",
            "供应链",
            "商业",
            "客户",
            "机队",
            "产能",
            "亚太",
            "全球机队",
            "市场预测",
            "市场走向",
            "市场特点",
            "market",
            "fleet",
            "delivery",
            "demand",
            "supply chain",
        ],
    },
    "technical_issue": {
        "query": [
            "技术问题",
            "故障",
            "问题",
            "缺陷",
            "失效",
            "裂纹",
            "召回",
            "检查",
            "改进",
            "风险",
            "可靠性",
            "寿命",
            "耐久",
            "停飞",
            "issue",
            "problem",
        ],
        "document": [
            "技术问题",
            "故障",
            "问题",
            "缺陷",
            "失效",
            "裂纹",
            "召回",
            "检查",
            "改进",
            "风险",
            "可靠性",
            "寿命",
            "耐久",
            "停飞",
            "延误",
            "粉末金属",
            "涡轮盘",
            "叶片",
            "封严",
            "磨损",
            "inspection",
            "issue",
            "problem",
            "durability",
            "reliability",
        ],
    },
    "performance_design": {
        "query": [
            "性能",
            "参数",
            "推力",
            "油耗",
            "效率",
            "排放",
            "构型",
            "优化",
            "热效率",
            "冷却",
            "燃烧",
            "压气机",
            "涡轮",
            "叶尖",
            "performance",
            "optimization",
        ],
        "document": [
            "性能",
            "参数",
            "推力",
            "油耗",
            "效率",
            "排放",
            "构型",
            "优化",
            "热效率",
            "冷却",
            "燃烧",
            "压气机",
            "涡轮",
            "叶尖",
            "叶片",
            "气热",
            "气动",
            "传热",
            "performance",
            "optimization",
            "cooling",
            "combustion",
            "compressor",
            "turbine",
        ],
    },
    "manufacturing_material": {
        "query": [
            "制造",
            "材料",
            "增材",
            "粉末",
            "涂层",
            "复合材料",
            "加工",
            "装配",
            "工艺",
            "manufacturing",
            "material",
        ],
        "document": [
            "制造",
            "材料",
            "增材",
            "粉末",
            "涂层",
            "复合材料",
            "加工",
            "装配",
            "工艺",
            "高温合金",
            "陶瓷基",
            "叶片制造",
            "manufacturing",
            "material",
            "additive",
            "coating",
            "powder",
        ],
    },
    "patent": {
        "query": [
            "专利",
            "知识产权",
            "技术布局",
            "专利布局",
            "patent",
        ],
        "document": [
            "专利",
            "知识产权",
            "技术布局",
            "专利布局",
            "德温特",
            "申请量",
            "patent",
        ],
    },
}
TOPIC_MIN_MATCH_SCORE = {
    "market": 18.0,
    "technical_issue": 12.0,
    "performance_design": 12.0,
    "manufacturing_material": 12.0,
}


@dataclass
class SelectedLocalPaper:
    title: str
    file_name: str
    source_path: str
    score: float
    author: str = ""
    abstract: str = ""
    source_type: str = "论文"


@dataclass
class LocalIndexSelection:
    query: str
    index_path: str
    pool_dir: str
    doc_path: str
    selected: list[SelectedLocalPaper]
    missing: list[str]
    reason: str = ""

    @property
    def has_documents(self) -> bool:
        return bool(self.selected)


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_default_local_docs_root() -> Path:
    return Path(os.getenv("LOCAL_DOCS_PATH", get_project_root() / "local_docs"))


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).lower()
    return re.sub(r"\s+", " ", text).strip()


def _normalize_filename(value: str) -> str:
    return FILENAME_CLEAN_RE.sub("", value).lower()


def _query_tokens(query: str) -> list[str]:
    tokens: set[str] = set()
    normalized = _normalize_text(query)

    for word in WORD_RE.findall(normalized):
        word = word.strip("._-/+")
        if len(word) >= 2:
            tokens.add(word)

    for block in CJK_RE.findall(query):
        if len(block) <= 12:
            tokens.add(block)
        for size in (2, 3, 4):
            if len(block) >= size:
                for i in range(len(block) - size + 1):
                    tokens.add(block[i : i + size])

    return sorted(tokens, key=lambda item: (-len(item), item))


def _paper_text(paper: dict[str, Any]) -> dict[str, str]:
    return {
        "title": _normalize_text(paper.get("title")),
        "author": _normalize_text(paper.get("author")),
        "applicant": _normalize_text(paper.get("applicant")),
        "abstract": _normalize_text(paper.get("abstract")),
        "file_name": _normalize_text(paper.get("file_name")),
        "source_type": _normalize_text(paper.get("source_type")),
        "source_library": _normalize_text(paper.get("source_library")),
        "keywords": _normalize_text(paper.get("keywords")),
    }


def _query_anchor_terms(query: str) -> list[str]:
    normalized = _normalize_text(query)
    anchors: set[str] = set()

    for key, aliases in MODEL_ANCHOR_ALIASES.items():
        if key in normalized:
            anchors.update(alias.lower() for alias in aliases)

    for word in WORD_RE.findall(normalized):
        clean_word = word.strip("._-/+").lower()
        if len(clean_word) >= 3 and any(char.isalpha() for char in clean_word):
            anchors.add(clean_word)

    return sorted(anchors, key=lambda item: (-len(item), item))


def _active_model_keys(query: str) -> list[str]:
    normalized = _normalize_text(query)
    return [key for key in MODEL_ANCHOR_ALIASES if key in normalized]


def _primary_anchor_terms(query: str) -> list[str]:
    anchors: set[str] = set()
    for key in _active_model_keys(query):
        anchors.update(term.lower() for term in MODEL_PRIMARY_ANCHORS.get(key, []))
    return sorted(anchors, key=lambda item: (-len(item), item))


def _combined_fields(fields: dict[str, str]) -> str:
    return " ".join(fields.values())


def _anchor_match_count(combined_text: str, anchors: list[str]) -> int:
    return sum(1 for anchor in anchors if anchor and anchor in combined_text)


def _has_primary_anchor(combined_text: str, primary_anchors: list[str]) -> bool:
    return any(anchor and anchor in combined_text for anchor in primary_anchors)


def _model_query_entry_allowed(query: str, paper: dict[str, Any], score: float) -> bool:
    active_models = _active_model_keys(query)
    if not active_models:
        return True

    fields = _paper_text(paper)
    combined_text = _combined_fields(fields)
    if _has_negative_context(combined_text):
        return False

    primary_anchors = _primary_anchor_terms(query)
    if _has_primary_anchor(combined_text, primary_anchors):
        return True

    anchor_terms = _query_anchor_terms(query)
    title_file_text = " ".join([fields["title"], fields["file_name"]])
    title_file_anchor_matches = _anchor_match_count(title_file_text, anchor_terms)
    total_anchor_matches = _anchor_match_count(combined_text, anchor_terms)
    if title_file_anchor_matches >= 1 and total_anchor_matches >= 2:
        return True
    if active_models == ["gtf"] and total_anchor_matches >= 1 and score >= 150 and _has_domain_context(combined_text):
        return True

    return False


def _has_negative_context(combined_text: str) -> bool:
    return any(term in combined_text for term in MODEL_NEGATIVE_CONTEXT)


def _has_domain_context(combined_text: str) -> bool:
    return any(term in combined_text for term in MODEL_DOMAIN_CONTEXT)


def _has_engine_domain_context(combined_text: str) -> bool:
    return any(term in combined_text for term in ENGINE_DOMAIN_TERMS)


def _query_requires_engine_domain(query: str) -> bool:
    normalized = _normalize_text(query)
    return any(term in normalized for term in ENGINE_QUERY_TERMS)


def _active_topic_keys(query: str) -> list[str]:
    normalized = _normalize_text(query)
    active: list[str] = []
    for key, profile in TOPIC_PROFILES.items():
        if any(term in normalized for term in profile["query"]):
            active.append(key)
    return active


def _topic_match_score(fields: dict[str, str], topic_keys: list[str]) -> float:
    return sum(_topic_profile_match_scores(fields, topic_keys).values())


def _topic_profile_match_scores(fields: dict[str, str], topic_keys: list[str]) -> dict[str, float]:
    if not topic_keys:
        return {}

    title_file_text = " ".join([fields["title"], fields["file_name"]])
    combined_text = _combined_fields(fields)
    scores: dict[str, float] = {}
    for key in topic_keys:
        profile = TOPIC_PROFILES.get(key, {})
        score = 0.0
        for term in profile.get("document", []):
            if not term:
                continue
            if term in title_file_text:
                score += 18.0
            elif term in combined_text:
                score += 6.0
        scores[key] = score
    return scores


def _has_topic_context(fields: dict[str, str], topic_keys: list[str]) -> bool:
    scores = _topic_profile_match_scores(fields, topic_keys)
    return any(score >= TOPIC_MIN_MATCH_SCORE.get(key, 6.0) for key, score in scores.items())


def _significant_query_overlap(query: str, fields: dict[str, str]) -> int:
    combined_text = _combined_fields(fields)
    ignore_terms = {"发动机", "航空", "商用", "民用", "动态", "跟踪", "研究", "分析", "问题", "影响"}
    tokens = [
        token
        for token in _query_tokens(query)
        if len(token) >= 3 and token not in ignore_terms
    ]
    return sum(1 for token in tokens if token in combined_text)


def _topic_query_entry_allowed(query: str, paper: dict[str, Any], score: float) -> bool:
    topic_keys = _active_topic_keys(query)
    if not topic_keys:
        return True

    fields = _paper_text(paper)
    combined_text = _combined_fields(fields)
    if _has_negative_context(combined_text):
        return False

    if _query_requires_engine_domain(query) and not _has_engine_domain_context(combined_text):
        return False

    if _has_topic_context(fields, topic_keys):
        return True

    # Fallback for sparse abstracts: keep a strongly matching engine-domain entry
    # when the title/file/abstract still overlaps with the user's concrete words.
    if (
        "market" not in topic_keys
        and score >= 90
        and _has_engine_domain_context(combined_text)
        and _significant_query_overlap(query, fields) >= 2
    ):
        return True

    return False


def _score_papers(query: str, papers: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    tokens = _query_tokens(query)
    if not tokens:
        return []
    anchor_terms = _query_anchor_terms(query)
    topic_keys = _active_topic_keys(query)

    paper_fields = [_paper_text(paper) for paper in papers]
    document_frequency: dict[str, int] = {}
    for token in tokens:
        document_frequency[token] = sum(
            1
            for fields in paper_fields
            if any(token in value for value in fields.values())
        )

    scored: list[tuple[float, dict[str, Any]]] = []
    total = max(len(papers), 1)
    for paper, fields in zip(papers, paper_fields):
        score = 0.0
        combined_text = _combined_fields(fields)
        anchor_matches = _anchor_match_count(combined_text, anchor_terms)
        topic_score = _topic_match_score(fields, topic_keys)
        for token in tokens:
            df = document_frequency.get(token, 0)
            if df == 0:
                continue
            idf = math.log((total + 1) / (df + 1)) + 1.0
            if token in fields["title"]:
                score += 8.0 * idf
            if token in fields["file_name"]:
                score += 6.0 * idf
            if token in fields["abstract"]:
                score += min(fields["abstract"].count(token), 3) * 1.6 * idf
            if token in fields["author"]:
                score += 0.5 * idf
            if token in fields.get("applicant", ""):
                score += 3.0 * idf
            if token in fields.get("source_type", ""):
                score += 2.0 * idf
            if token in fields.get("source_library", ""):
                score += 1.2 * idf
            if token in fields.get("keywords", ""):
                score += 2.0 * idf

        full_query = _normalize_text(query)
        if full_query and full_query in combined_text:
            score += 20.0

        if anchor_terms:
            if anchor_matches:
                score += 120.0 * anchor_matches
            elif _has_negative_context(combined_text):
                score = 0.0
            else:
                score *= 0.2

        if topic_keys:
            if topic_score:
                score += topic_score
            elif _query_requires_engine_domain(query) and not _has_engine_domain_context(combined_text):
                score = 0.0
            else:
                score *= 0.55

        if score > 0:
            scored.append((score, paper))

    return sorted(scored, key=lambda item: item[0], reverse=True)


def _load_index(index_path: Path) -> list[dict[str, Any]]:
    with index_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    raise ValueError(f"Expected a list in local docs index: {index_path}")


def _resolve_paper_path(paper: dict[str, Any], pool_files: list[Path]) -> Path | None:
    requested_name = str(paper.get("file_name") or "").strip()
    title = str(paper.get("title") or "").strip()
    if not requested_name and title:
        requested_name = f"{title}.pdf"

    by_lower_name = {path.name.lower(): path for path in pool_files}
    exact = by_lower_name.get(requested_name.lower())
    if exact:
        return exact

    title_key = _normalize_filename(Path(requested_name).stem or title)
    if not title_key:
        return None

    candidates = []
    for path in pool_files:
        stem_key = _normalize_filename(path.stem)
        if stem_key.startswith(title_key) or title_key in stem_key:
            candidates.append(path)

    if not candidates:
        return None

    return sorted(candidates, key=lambda path: (len(path.stem), path.name))[0]


def _virtual_patent_doc_name(paper: dict[str, Any]) -> str:
    row_number = str(paper.get("row_number") or "0")
    title = str(paper.get("title") or "patent").strip()
    stem = FILENAME_CLEAN_RE.sub("_", title).strip("._")[:70] or "patent"
    return f"patent_record_{row_number}_{stem}.txt"


def _write_virtual_patent_doc(paper: dict[str, Any], selected_dir: Path) -> Path:
    destination = selected_dir / _virtual_patent_doc_name(paper)
    lines = [
        f"专利题名：{paper.get('title') or '-'}",
        f"来源类型：{paper.get('source_type') or '专利'}",
        f"来源库：{paper.get('source_library') or '-'}",
        f"申请人：{paper.get('applicant') or '-'}",
        f"作者/发明人：{paper.get('author') or '-'}",
        f"来源表格：{paper.get('source_workbook') or '-'}",
        f"表格行号：{paper.get('row_number') or '-'}",
        "",
        "摘要：",
        str(paper.get("abstract") or "无摘要内容"),
    ]
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination


def _prepare_temp_dir(query: str, temp_root: Path | None = None) -> Path:
    root = Path(os.getenv("LOCAL_DOCS_TEMP_PATH", temp_root or get_project_root() / "temp_docs"))
    root = root.resolve()
    digest = hashlib.md5(f"{query}-{time.time()}".encode("utf-8")).hexdigest()[:12]
    target = (root / f"local_{digest}").resolve()

    if target.exists():
        if root not in target.parents:
            raise ValueError(f"Refusing to clear a directory outside temp root: {target}")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def should_use_local_index(doc_path: str | os.PathLike[str] | None = None) -> bool:
    local_root = get_default_local_docs_root().resolve()
    index_path = Path(os.getenv("LOCAL_DOCS_INDEX_PATH", local_root / "papers_index.json"))
    if not index_path.exists():
        return False

    if not doc_path:
        return True

    try:
        resolved_doc_path = Path(doc_path).resolve()
    except Exception:
        return True

    default_doc_path = (get_project_root() / "my-docs").resolve()
    return (
        resolved_doc_path == local_root
        or resolved_doc_path == default_doc_path
        or str(resolved_doc_path).endswith("my-docs")
    )


def _select_index_documents(
    *,
    query: str,
    index_path: Path,
    pool_dir: Path,
    selected_dir: Path,
    max_docs: int,
    source_type: str,
    allow_virtual_records: bool = False,
) -> tuple[list[SelectedLocalPaper], list[str], str]:
    if not index_path.exists():
        return [], [], f"Index file not found: {index_path}"
    if not pool_dir.exists():
        return [], [], f"Document pool not found: {pool_dir}"

    indexed_docs = _load_index(index_path)
    ranked = _score_papers(query, indexed_docs)
    if not ranked:
        return [], [], f"No {source_type} index entries matched the query."

    pool_files = [
        path
        for path in pool_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    selected: list[SelectedLocalPaper] = []
    missing: list[str] = []

    for score, paper in ranked:
        if len(selected) >= max_docs:
            break

        if not _model_query_entry_allowed(query, paper, score):
            continue
        if not _topic_query_entry_allowed(query, paper, score):
            continue

        source_path = _resolve_paper_path(paper, pool_files)
        requested_name = str(paper.get("file_name") or paper.get("title") or "").strip()
        if not source_path:
            if not allow_virtual_records:
                missing.append(requested_name)
                continue
            destination = _write_virtual_patent_doc(paper, selected_dir)
            source_path = destination
        else:
            destination = selected_dir / source_path.name
            if not destination.exists():
                shutil.copy2(source_path, destination)
        selected.append(
            SelectedLocalPaper(
                title=str(paper.get("title") or source_path.stem),
                file_name=destination.name,
                source_path=str(source_path),
                score=round(score, 3),
                author=str(paper.get("author") or paper.get("applicant") or ""),
                abstract=str(paper.get("abstract") or "")[:500],
                source_type=source_type,
            )
        )

    reason = "" if selected else f"Matched {source_type} index entries, but none of their files were found."
    return selected, missing, reason


def prepare_local_docs_for_query(
    query: str,
    max_docs: int = 5,
    local_docs_root: str | os.PathLike[str] | None = None,
    temp_root: str | os.PathLike[str] | None = None,
    include_papers: bool = True,
    include_user_docs: bool = True,
    include_patents: bool = True,
) -> LocalIndexSelection:
    local_root = Path(local_docs_root or get_default_local_docs_root()).resolve()
    index_path = Path(os.getenv("LOCAL_DOCS_INDEX_PATH", local_root / "papers_index.json")).resolve()
    pool_dir = Path(os.getenv("LOCAL_DOCS_POOL_PATH", local_root / "all_papers_pool")).resolve()

    empty = LocalIndexSelection(
        query=query,
        index_path=str(index_path),
        pool_dir=str(pool_dir),
        doc_path="",
        selected=[],
        missing=[],
    )

    selected_dir = _prepare_temp_dir(query, Path(temp_root) if temp_root else None)
    max_docs = max(1, min(int(max_docs or 5), 12))
    selected: list[SelectedLocalPaper] = []
    missing: list[str] = []
    reasons: list[str] = []

    if include_papers:
        paper_selected, paper_missing, paper_reason = _select_index_documents(
            query=query,
            index_path=index_path,
            pool_dir=pool_dir,
            selected_dir=selected_dir,
            max_docs=max_docs,
            source_type="论文",
        )
        selected.extend(paper_selected)
        missing.extend(paper_missing)
        if paper_reason:
            reasons.append(paper_reason)

    if include_user_docs:
        user_selected, user_missing, user_reason = _select_index_documents(
            query=query,
            index_path=get_user_docs_index_path(),
            pool_dir=get_user_docs_dir(),
            selected_dir=selected_dir,
            max_docs=max(1, min(max_docs, 5)),
            source_type="用户资料",
        )
        selected.extend(user_selected)
        missing.extend(user_missing)
        if user_reason:
            reasons.append(user_reason)

    if include_patents:
        patent_selected, patent_missing, patent_reason = _select_index_documents(
            query=query,
            index_path=get_patents_index_path(),
            pool_dir=get_patent_pool_dir(),
            selected_dir=selected_dir,
            max_docs=max(1, min(max_docs, 5)),
            source_type="专利",
            allow_virtual_records=True,
        )
        selected.extend(patent_selected)
        missing.extend(patent_missing)
        if patent_reason:
            reasons.append(patent_reason)

    sidecar = selected_dir / "selected_sources.json"
    with sidecar.open("w", encoding="utf-8") as handle:
        json.dump([asdict(item) for item in selected], handle, ensure_ascii=False, indent=2)

    if not selected:
        empty.doc_path = str(selected_dir)
        empty.missing = missing
        empty.reason = "；".join(reasons) or "No local index entries matched the query."
        return empty

    return LocalIndexSelection(
        query=query,
        index_path=str(index_path),
        pool_dir=str(pool_dir),
        doc_path=str(selected_dir),
        selected=selected,
        missing=missing,
        reason="",
    )
