from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from gpt_researcher.evaluation.entity_evaluator import (
    URL_RE,
    _extract_evidence_map,
    _important_terms,
    _numbers_in_text,
    _read_url_text,
    _term_hit_count,
)


PUBLIC_URL_SOURCE_THRESHOLD = 0.98
URL_REF_RE = re.compile(r"\[URL\s*\d+\]", re.IGNORECASE)
LOCAL_REF_RE = re.compile(r"\[(?:原文|文献)\s*\d+\]", re.IGNORECASE)


def _analysis_report_body(report: str) -> str:
    text = report or ""
    cut_markers = [
        "\n## 本次精读文献清单",
        "\n## 本次运行统计",
        "\n## 运行统计",
    ]
    end = len(text)
    for marker in cut_markers:
        index = text.find(marker)
        if index >= 0:
            end = min(end, index)
    return text[:end]


def _line_is_evidence_source(line: str) -> bool:
    stripped = line.strip().lstrip("-*").strip()
    return bool(re.match(r"^\[(?:URL|原文|来源|文献)\s*\d*\]\s+", stripped, flags=re.IGNORECASE))


def _strip_markdown_for_claim(text: str) -> str:
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text or "")
    cleaned = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", cleaned)
    cleaned = re.sub(r"`[^`]*`", " ", cleaned)
    cleaned = re.sub(r"</?[^>]+>", " ", cleaned)
    cleaned = re.sub(r"^\s*\|?[-:\s|]+\|?\s*$", " ", cleaned)
    cleaned = URL_REF_RE.sub(" ", cleaned)
    cleaned = URL_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\[(?:原文|来源|文献)\s*\d*\]", " ", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("|", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -*#；;。")


def _extract_url_reference_contexts(report: str) -> Dict[str, List[str]]:
    body = _analysis_report_body(report)
    contexts: Dict[str, List[str]] = {}
    in_evidence_list = False

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "证据来源" in stripped or "来源列表" in stripped:
            in_evidence_list = True
            continue
        if in_evidence_list:
            if stripped.startswith("#") and "证据来源" not in stripped and "来源列表" not in stripped:
                in_evidence_list = False
            elif _line_is_evidence_source(stripped):
                continue

        refs = [match.group(0) for match in URL_REF_RE.finditer(stripped)]
        if not refs:
            continue

        claim = _strip_markdown_for_claim(stripped)
        if not claim:
            continue
        for ref in refs:
            contexts.setdefault(ref, [])
            if claim not in contexts[ref]:
                contexts[ref].append(claim)

    return contexts


def _body_and_tail(report: str) -> tuple[str, str]:
    text = report or ""
    cut_markers = [
        "\n## 本次精读文献清单",
        "\n## 本次运行统计",
        "\n## 运行统计",
    ]
    end = len(text)
    for marker in cut_markers:
        index = text.find(marker)
        if index >= 0:
            end = min(end, index)
    return text[:end], text[end:]


def _url_refs_in_text(text: str) -> List[str]:
    return [match.group(0) for match in URL_REF_RE.finditer(text or "")]


def _cited_url_refs_outside_evidence_list(text: str) -> set[str]:
    refs: set[str] = set()
    for line in (text or "").splitlines():
        stripped = line.strip()
        if _line_is_evidence_source(stripped):
            continue
        refs.update(_normalize_ref(ref) for ref in _url_refs_in_text(line))
    return refs


def _normalize_ref(ref: str) -> str:
    number = re.search(r"\d+", ref or "")
    return f"[URL{number.group(0)}]" if number else ref.upper()


def _line_has_local_evidence(line: str) -> bool:
    return bool(LOCAL_REF_RE.search(line or ""))


def _citation_sort_key(ref: str) -> int:
    number = re.search(r"\d+", ref or "")
    return int(number.group(0)) if number else 0


def _split_claim_segments(line: str) -> List[str]:
    """Split a paragraph/table row into smaller claim units while keeping refs."""
    text = line or ""
    segments: List[str] = []
    start = 0
    for match in re.finditer(r"[。！？!?；;]\s*|\s+\|\s+", text):
        end = match.end()
        segment = text[start:end].strip()
        if segment:
            segments.append(segment)
        start = end
    tail = text[start:].strip()
    if tail:
        segments.append(tail)
    return segments or ([text] if text else [])


def _nearest_local_ref(segment: str, url_ref: str) -> str:
    url_match = re.search(re.escape(url_ref), segment or "", flags=re.IGNORECASE)
    if not url_match:
        return ""

    local_matches = list(LOCAL_REF_RE.finditer(segment or ""))
    if not local_matches:
        return ""

    preceding = [match for match in local_matches if match.end() <= url_match.start()]
    if preceding:
        return preceding[-1].group(0)

    nearest = min(local_matches, key=lambda match: abs(match.start() - url_match.end()))
    return nearest.group(0)


def _patent_url_can_use_local_source(url: str) -> bool:
    lowered = (url or "").lower()
    return "patents.google." in lowered or "/patent/" in lowered


def prune_redundant_unchecked_url_citations(
    report: str,
    source_eval: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    """Remove unchecked URL refs when the same claim already has local evidence.

    The function is conservative: it only removes an unchecked [URLn] from a
    line/table row that also contains a local [原文n]/[文献n] citation. If that
    URL is no longer cited anywhere in the analytical body, its evidence-list
    line is removed too.
    """
    unchecked_refs = {
        _normalize_ref(str(item.get("ref") or ""))
        for item in source_eval.get("results", [])
        if item.get("status") == "unchecked"
    }
    if not unchecked_refs:
        return report, {
            "removed_redundant_url_refs": [],
            "removed_evidence_source_refs": [],
            "replaced_url_refs_with_local_refs": {},
            "sole_unchecked_url_risks": [],
            "changed": False,
        }

    body, tail = _body_and_tail(report)
    url_by_ref = {
        _normalize_ref(str(item.get("ref") or "")): str(item.get("url") or "")
        for item in source_eval.get("results", [])
    }
    replacement_by_ref: Dict[str, str] = {}
    for line in body.splitlines():
        for segment in _split_claim_segments(line):
            if not _line_has_local_evidence(segment):
                continue
            refs_in_segment = {_normalize_ref(ref) for ref in _url_refs_in_text(segment)}
            for ref in refs_in_segment & unchecked_refs:
                if _patent_url_can_use_local_source(url_by_ref.get(ref, "")):
                    replacement = _nearest_local_ref(segment, ref)
                    if replacement:
                        replacement_by_ref.setdefault(ref, replacement)

    removed_refs: set[str] = set()
    remaining_unchecked_refs: set[str] = set()
    new_lines: List[str] = []

    for line in body.splitlines(keepends=True):
        if _line_is_evidence_source(line.strip()):
            new_lines.append(line)
            continue

        refs_in_line = {_normalize_ref(ref) for ref in _url_refs_in_text(line)}
        removable_in_line = refs_in_line & unchecked_refs
        if removable_in_line:
            updated = line
            removed_in_line: set[str] = set()
            for segment in _split_claim_segments(line):
                if not _line_has_local_evidence(segment):
                    continue
                refs_in_segment = {_normalize_ref(ref) for ref in _url_refs_in_text(segment)}
                for ref in refs_in_segment & removable_in_line:
                    updated = re.sub(re.escape(ref), "", updated, flags=re.IGNORECASE)
                    removed_refs.add(ref)
                    removed_in_line.add(ref)

            for ref in sorted(removable_in_line - removed_in_line, key=_citation_sort_key):
                replacement = replacement_by_ref.get(ref)
                if replacement:
                    updated = re.sub(re.escape(ref), replacement, updated, flags=re.IGNORECASE)
                    removed_refs.add(ref)
                    removed_in_line.add(ref)

            remaining_unchecked_refs.update(removable_in_line - removed_in_line)
            updated = re.sub(r"(\])\s+(\[)", r"\1\2", updated)
            updated = re.sub(r"\s{2,}", " ", updated)
            new_lines.append(updated)
        else:
            if removable_in_line:
                remaining_unchecked_refs.update(removable_in_line)
            new_lines.append(line)

    cleaned_body = "".join(new_lines)
    body_refs_after = _cited_url_refs_outside_evidence_list(cleaned_body)
    refs_to_drop_from_sources = removed_refs - body_refs_after

    final_lines: List[str] = []
    dropped_source_refs: set[str] = set()
    for line in cleaned_body.splitlines(keepends=True):
        stripped = line.strip().lstrip("-*").strip()
        match = re.match(r"^(\[URL\s*\d+\])\s+", stripped, flags=re.IGNORECASE)
        if match and _normalize_ref(match.group(1)) in refs_to_drop_from_sources:
            dropped_source_refs.add(_normalize_ref(match.group(1)))
            continue
        final_lines.append(line)

    cleaned_report = "".join(final_lines) + tail
    sole_risks = []
    for item in source_eval.get("results", []):
        ref = _normalize_ref(str(item.get("ref") or ""))
        if ref in unchecked_refs and ref not in dropped_source_refs:
            sole_risks.append(
                {
                    "ref": item.get("ref"),
                    "url": item.get("url"),
                    "status": item.get("status"),
                    "reason": "该 URL 无法自动读取，且未被本地原文证据替代，保留为公开溯源风险。",
                    "context_preview": item.get("context_preview", []),
                }
            )

    return cleaned_report, {
        "removed_redundant_url_refs": sorted(removed_refs),
        "removed_evidence_source_refs": sorted(dropped_source_refs),
        "replaced_url_refs_with_local_refs": {
            ref: local_ref for ref, local_ref in replacement_by_ref.items() if ref in removed_refs
        },
        "sole_unchecked_url_risks": sole_risks,
        "changed": cleaned_report != report,
    }


def _url_for_ref(ref: str, evidence_map: Dict[str, str]) -> str:
    mapped = evidence_map.get(ref, "") or evidence_map.get(ref.upper(), "")
    match = URL_RE.search(mapped)
    return match.group(0) if match else ""


def _claim_terms_for_matching(claim: str) -> List[str]:
    raw_terms = _important_terms(claim, max_terms=24)
    weak_terms = {
        "报告",
        "显示",
        "披露",
        "指出",
        "相关",
        "当前",
        "本次",
        "来源",
        "证据",
        "材料",
        "数据",
        "分析",
        "支撑",
        "可见",
        "认为",
        "涉及",
    }
    terms = []
    for term in raw_terms:
        stripped = str(term or "").strip()
        if not stripped or stripped.lower() in weak_terms or stripped in weak_terms:
            continue
        terms.append(stripped)
    return terms[:18]


def _check_claims_against_source(claims: List[str], source_text: str) -> Dict[str, Any]:
    if not source_text:
        return {
            "status": "unchecked",
            "confidence": 0.0,
            "reason": "URL 正文无法读取，未能执行内容支撑核验。",
            "matched_terms": [],
            "matched_numbers": [],
        }

    combined_claim = "；".join(claims)
    terms = _claim_terms_for_matching(combined_claim)
    numbers = _numbers_in_text(combined_claim)
    matched_terms = [term for term in terms if _term_hit_count([term], source_text)]
    matched_numbers = [number for number in numbers if _term_hit_count([number], source_text)]

    term_score = len(matched_terms) / max(len(terms), 1)
    number_score = len(matched_numbers) / max(len(numbers), 1) if numbers else 1.0
    confidence = round(min(1.0, 0.75 * term_score + 0.25 * number_score), 4)

    has_enough_terms = len(matched_terms) >= 2 or term_score >= 0.45
    numbers_ok = not numbers or bool(matched_numbers) or number_score >= 0.5

    if has_enough_terms and numbers_ok:
        return {
            "status": "supported",
            "confidence": confidence,
            "reason": "URL 正文命中引用句的关键实体/主题词，并满足主要数字或日期约束。",
            "matched_terms": matched_terms[:12],
            "matched_numbers": matched_numbers[:8],
        }
    if matched_terms or matched_numbers:
        return {
            "status": "partially_supported",
            "confidence": confidence,
            "reason": "URL 正文命中部分关键词或数字，但不足以完全支撑引用句。",
            "matched_terms": matched_terms[:12],
            "matched_numbers": matched_numbers[:8],
        }
    return {
        "status": "unsupported",
        "confidence": confidence,
        "reason": "URL 正文可读取，但未命中引用句的关键实体、主题词或数字。",
        "matched_terms": [],
        "matched_numbers": [],
    }


def evaluate_public_url_sources(report: str, threshold: float = PUBLIC_URL_SOURCE_THRESHOLD) -> Dict[str, Any]:
    evidence_map = _extract_evidence_map(report or "")
    contexts_by_ref = _extract_url_reference_contexts(report or "")
    ordered_refs = sorted(
        contexts_by_ref,
        key=lambda value: int(re.search(r"\d+", value).group(0)) if re.search(r"\d+", value) else 0,
    )
    results_by_ref: Dict[str, Dict[str, Any]] = {}
    fetch_jobs: Dict[Any, tuple[str, str, List[str]]] = {}

    with ThreadPoolExecutor(max_workers=max(1, min(8, len(ordered_refs) or 1))) as executor:
        for ref in ordered_refs:
            url = _url_for_ref(ref, evidence_map)
            contexts = contexts_by_ref[ref]
            if not url:
                results_by_ref[ref] = {
                    "ref": ref,
                    "url": "",
                    "status": "unchecked",
                    "confidence": 0.0,
                    "citation_count": len(contexts),
                    "context_preview": contexts[:3],
                    "reason": "正文引用了该 URL 编号，但证据来源列表中未找到对应真实 URL。",
                    "matched_terms": [],
                    "matched_numbers": [],
                    "source_readable": False,
                }
                continue
            future = executor.submit(_read_url_text, url)
            fetch_jobs[future] = (ref, url, contexts)

        for future in as_completed(fetch_jobs):
            ref, url, contexts = fetch_jobs[future]
            try:
                source_text = future.result()
            except Exception:
                source_text = ""
            check = _check_claims_against_source(contexts, source_text)
            results_by_ref[ref] = {
                "ref": ref,
                "url": url,
                "status": check["status"],
                "confidence": check["confidence"],
                "citation_count": len(contexts),
                "context_preview": contexts[:3],
                "reason": check["reason"],
                "matched_terms": check["matched_terms"],
                "matched_numbers": check["matched_numbers"],
                "source_readable": bool(source_text),
            }

    results = [results_by_ref[ref] for ref in ordered_refs if ref in results_by_ref]

    supported = sum(1 for item in results if item["status"] == "supported")
    partial = sum(1 for item in results if item["status"] == "partially_supported")
    unsupported = sum(1 for item in results if item["status"] == "unsupported")
    unchecked = sum(1 for item in results if item["status"] == "unchecked")
    cited_count = len(results)
    weighted_score = supported + 0.5 * partial
    support_accuracy = round(weighted_score / cited_count, 4) if cited_count else None
    checked_count = supported + partial + unsupported
    checked_support_accuracy = round(weighted_score / checked_count, 4) if checked_count else None

    return {
        "method": "url_citation_context_source_text_matching",
        "threshold": threshold,
        "cited_url_ref_count": cited_count,
        "supported_count": supported,
        "partially_supported_count": partial,
        "unsupported_count": unsupported,
        "unchecked_count": unchecked,
        "checked_count": checked_count,
        "support_accuracy": support_accuracy,
        "checked_support_accuracy": checked_support_accuracy,
        "requirement_met": support_accuracy is not None and support_accuracy >= threshold,
        "results": results,
        "note": "该指标按正文中被 [URLn] 引用的公开链接计算，核验链接正文是否支撑引用句；无法读取的 URL 按未核验计入分母。",
    }
