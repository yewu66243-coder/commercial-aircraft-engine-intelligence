"""Build a safe, stable, presentation-only evaluation summary."""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import unquote


ENTITY_THRESHOLD = 0.90
PUBLIC_LINK_ACCESSIBILITY_THRESHOLD = 0.98
PUBLIC_LINK_SUPPORT_THRESHOLD = 0.90
PUBLIC_LINK_THRESHOLD = PUBLIC_LINK_ACCESSIBILITY_THRESHOLD  # legacy import alias

_COUNT_FIELDS = ("true_positive", "false_positive", "false_negative")
_RATE_FIELDS = ("precision", "recall", "f1")
_ACCESSIBILITY_FIELDS = {
    "total_count": "total_urls",
    "checked_count": "checked_urls",
    "accessible_count": "accessible_urls",
    "inaccessible_count": "failed_urls",
}
_SUPPORT_FIELDS = (
    "relationship_count", "supported_count", "partially_supported_count",
    "unsupported_count", "unchecked_count",
)


def _error(scope: str, code: str, message: str) -> dict[str, str]:
    return {"scope": scope, "code": code, "message": message}


def _input_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _safe_string(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_file_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        candidate = value.replace("\\", "/").rsplit("/", 1)[-1]
    except Exception:
        return ""
    return "" if candidate in {"", ".", ".."} else candidate


def _count(value: Any, *, missing_is_valid: bool = False) -> tuple[int | None, bool]:
    if value is None:
        return (0, True) if missing_is_valid else (None, False)
    try:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None, False
    except Exception:
        return None, False
    return value, True


def _rate(value: Any, *, missing_is_valid: bool = False) -> tuple[float | None, bool]:
    if value is None:
        return (None, True) if missing_is_valid else (None, False)
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, False
        numeric = float(value)
        if not math.isfinite(numeric) or not 0 <= numeric <= 1:
            return None, False
    except Exception:
        return None, False
    return numeric, True


def _safe_list(value: Any) -> list[Any]:
    try:
        return list(value) if isinstance(value, list) else []
    except Exception:
        return []


def _safe_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _public_item(item: Any, allowed: tuple[str, ...], bool_fields: tuple[str, ...] = ()) -> Any:
    """Deep-build a presentation item from a fixed field whitelist."""
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return None
    safe: dict[str, Any] = {}
    for key in allowed:
        if key not in item:
            continue
        if key in bool_fields:
            value = _safe_bool(item.get(key))
            if value is not None:
                safe[key] = value
        elif key == "confidence":
            value, valid = _rate(item.get(key))
            if valid:
                safe[key] = value
        elif key in {"matched_terms", "matched_numbers"}:
            values = _safe_list(item.get(key))
            safe[key] = [_safe_string(value) for value in values if _safe_string(value)]
        elif key == "status_code":
            value, valid = _count(item.get(key))
            if valid:
                safe[key] = value
        else:
            value = _safe_string(item.get(key))
            if value:
                safe[key] = value
    return safe


def _entity_detail_list(value: Any) -> list[Any]:
    allowed = ("entity", "name", "category", "value", "unit", "description", "evidence_supported")
    output: list[Any] = []
    for item in _safe_list(value):
        if isinstance(item, dict) and (isinstance(item.get("predicted"), dict) or isinstance(item.get("expected"), dict)):
            match: dict[str, Any] = {}
            for key in ("category", "match_type"):
                text = _safe_string(item.get(key))
                if text:
                    match[key] = text[:256]
            for key in ("predicted", "expected"):
                candidate = item.get(key)
                if not isinstance(candidate, dict):
                    continue
                projected: dict[str, Any] = {}
                for field in ("type", "category", "name", "value", "unit", "description"):
                    text = _safe_string(candidate.get(field))
                    if text:
                        projected[field] = text[:256]
                aliases = [_safe_string(alias)[:128] for alias in _safe_list(candidate.get("aliases"))]
                aliases = [alias for alias in aliases if alias][:20]
                if aliases:
                    projected["aliases"] = aliases
                match[key] = projected
            output.append(match)
            continue
        safe = _public_item(item, allowed, ("evidence_supported",))
        if safe is not None:
            output.append(safe)
    return output


def _entity_details(source: dict[str, Any], metrics: dict[str, Any]) -> tuple[list[Any], list[Any], list[Any]]:
    def first(*names: str) -> list[Any]:
        for container in (source, metrics):
            for name in names:
                if isinstance(container.get(name), list):
                    return _entity_detail_list(container[name])
        return []

    return (
        first("matched", "matches", "correct_entities"),
        first("false_positives", "wrong_entities"),
        first("false_negatives", "missed_entities"),
    )


def _metric_values(value: Any) -> tuple[dict[str, int], dict[str, float | None], bool]:
    if not isinstance(value, dict) or any(key not in value for key in (*_COUNT_FIELDS, *_RATE_FIELDS)):
        return {}, {}, False
    counts: dict[str, int] = {}
    for key in _COUNT_FIELDS:
        count, valid = _count(value.get(key))
        if not valid or count is None:
            return {}, {}, False
        counts[key] = count
    tp, fp, fn = counts["true_positive"], counts["false_positive"], counts["false_negative"]
    precision = round(tp / (tp + fp), 4) if tp + fp else None
    recall = round(tp / (tp + fn), 4) if tp + fn else None
    f1 = round(2 * tp / (2 * tp + fp + fn), 4) if 2 * tp + fp + fn else None
    rates = {"precision": precision, "recall": recall, "f1": f1}
    return counts, rates, True


def _validated_categories(value: Any) -> tuple[dict[str, dict[str, Any]], bool]:
    if value is None:
        return {}, True
    if not isinstance(value, dict):
        return {}, False
    categories: dict[str, dict[str, Any]] = {}
    for category, metrics in value.items():
        counts, rates, valid = _metric_values(metrics)
        if not valid:
            return {}, False
        label = _safe_string(metrics.get("label")) or _safe_string(category)
        categories[_safe_string(category)] = {
            "label": label,
            **counts,
            **rates,
            "requirement_met": None if rates["f1"] is None else rates["f1"] >= ENTITY_THRESHOLD,
        }
    return categories, True


def _failed_entity(mode: str = "proxy", status: str = "evaluation_failed") -> dict[str, Any]:
    return {
        "mode": mode,
        "status": status,
        "ground_truth_path": "",
        "threshold": ENTITY_THRESHOLD,
        "overall": None,
        "categories": {},
        "matched": [],
        "false_positives": [],
        "false_negatives": [],
        "proxy_evidence_support_rate": None,
        "message": "实体抽取测评未完成。" if status == "evaluation_failed" else "标准答案无效，未执行严格实体评估。",
    }


def _entity_summary(value: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    source = _input_dict(value)
    if source is None:
        return _failed_entity(), [_error("entity", "evaluation_failed", "实体抽取测评未完成。")]
    raw_mode = source.get("mode")
    mode = raw_mode if isinstance(raw_mode, str) and raw_mode in {"strict", "proxy", "invalid"} else "proxy"
    if source.get("status") == "evaluation_failed":
        return _failed_entity(mode), [_error("entity", "evaluation_failed", "实体抽取测评未完成。")]
    if mode == "invalid":
        return _failed_entity("invalid", "invalid_ground_truth"), [_error("entity", "invalid_ground_truth", "标准答案文件无效。")]
    metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
    if mode == "proxy":
        auto = source.get("auto_evidence_eval") if isinstance(source.get("auto_evidence_eval"), dict) else {}
        rate, valid = _rate(auto.get("auto_evidence_accuracy"), missing_is_valid=True)
        if not valid:
            return _failed_entity(mode), [_error("entity", "evaluation_failed", "实体抽取测评未完成。")]
        matched, false_positives, false_negatives = _entity_details(source, metrics)
        return ({
            "mode": "proxy", "status": "proxy", "ground_truth_path": _safe_file_name(source.get("ground_truth_path")),
            "threshold": ENTITY_THRESHOLD, "overall": None, "categories": {}, "matched": matched,
            "false_positives": false_positives, "false_negatives": false_negatives,
            "proxy_evidence_support_rate": rate, "message": "已完成自动证据核验，严格实体指标仍需标准答案。",
        }, [])
    counts, rates, valid_overall = _metric_values(metrics.get("overall"))
    categories, valid_categories = _validated_categories(metrics.get("categories"))
    if not valid_overall or not valid_categories:
        return _failed_entity("strict"), [_error("entity", "evaluation_failed", "实体抽取测评未完成。")]
    matched, false_positives, false_negatives = _entity_details(source, metrics)
    return ({
        "mode": "strict", "status": "completed", "ground_truth_path": _safe_file_name(source.get("ground_truth_path")),
        "threshold": ENTITY_THRESHOLD,
        "overall": {**counts, **rates, "requirement_met": None if rates["f1"] is None else rates["f1"] >= ENTITY_THRESHOLD},
        "categories": categories, "matched": matched, "false_positives": false_positives,
        "false_negatives": false_negatives, "proxy_evidence_support_rate": None,
        "message": "已根据任务金标准计算严格实体指标。",
    }, [])


def _failed_accessibility() -> tuple[dict[str, Any], list[dict[str, str]]]:
    return ({
        "status": "evaluation_failed", "threshold": PUBLIC_LINK_ACCESSIBILITY_THRESHOLD,
        "total_count": 0, "checked_count": 0, "accessible_count": 0, "inaccessible_count": 0,
        "rate": None, "requirement_met": None, "results": [],
    }, [_error("public_links", "evaluation_failed", "公开链接可访问性测评未完成。")])


def _accessibility_results(value: Any) -> list[Any]:
    fields = ("url", "checked_url", "status_code", "accessible", "method", "ssl_verified")
    allowed_reasons = {"accessible", "http_status", "connection", "timeout", "ssl_certificate", "checker_exception", "invalid_url", "invalid_url_encoding", "dns_or_host", "userinfo", "network_or_unknown"}
    allowed_warnings = {"ssl_unverified", "redirected", "head_fallback"}
    result: list[Any] = []
    for item in _safe_list(value):
        safe = _public_item(item, fields, ("accessible", "ssl_verified"))
        if isinstance(safe, dict):
            raw_reason = item.get("failure_reason") if isinstance(item, dict) else None
            if raw_reason is not None:
                reason = _safe_string(raw_reason)
                safe["failure_reason"] = reason if reason in allowed_reasons else "network_or_unknown"
            raw_warning = item.get("warning") if isinstance(item, dict) else None
            warning = _safe_string(raw_warning)
            if warning in allowed_warnings:
                safe["warning"] = warning
            result.append(safe)
    return result


def _accessibility_summary(value: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    source = _input_dict(value)
    if source is None or source.get("evaluation_error") is not None:
        return _failed_accessibility()
    total, total_valid = _count(source.get("total_urls"))
    if not total_valid or total is None:
        return _failed_accessibility()
    if total == 0:
        zero_values = (source.get("checked_urls"), source.get("accessible_urls"), source.get("failed_urls"))
        for item in zero_values:
            count, valid = _count(item, missing_is_valid=True)
            if not valid or count != 0:
                return _failed_accessibility()
        return ({"status": "no_public_urls", "threshold": PUBLIC_LINK_ACCESSIBILITY_THRESHOLD,
                 "total_count": 0, "checked_count": 0, "accessible_count": 0, "inaccessible_count": 0,
                 "rate": None, "requirement_met": None, "results": []}, [])
    counts: dict[str, int] = {}
    for target, source_key in _ACCESSIBILITY_FIELDS.items():
        count, valid = _count(source.get(source_key))
        if not valid or count is None:
            return _failed_accessibility()
        counts[target] = count
    total, checked = counts["total_count"], counts["checked_count"]
    skipped_raw = source.get("skipped_urls")
    skipped = total - checked if skipped_raw is None else _count(skipped_raw)[0]
    if skipped is None or checked > total or skipped + checked != total or counts["accessible_count"] + counts["inaccessible_count"] != checked:
        return _failed_accessibility()
    results = _accessibility_results(source.get("results"))
    if not isinstance(source.get("results"), list) or len(results) != checked:
        return _failed_accessibility()
    rate = round(counts["accessible_count"] / checked, 4) if checked else None
    return ({"status": "completed", "threshold": PUBLIC_LINK_ACCESSIBILITY_THRESHOLD, **counts,
             "rate": rate, "requirement_met": None if rate is None else rate >= PUBLIC_LINK_ACCESSIBILITY_THRESHOLD,
             "results": results}, [])


def _not_evaluated_claim_support() -> dict[str, Any]:
    return {"status": "not_evaluated", "threshold": PUBLIC_LINK_SUPPORT_THRESHOLD,
            "relationship_count": 0, "supported_count": 0, "partially_supported_count": 0,
            "unsupported_count": 0, "unchecked_count": 0, "accuracy": None,
            "requirement_met": None, "relationships": []}


def _failed_claim_support() -> tuple[dict[str, Any], list[dict[str, str]]]:
    failed = _not_evaluated_claim_support()
    failed["status"] = "evaluation_failed"
    return failed, [_error("public_links", "evaluation_failed", "断言—链接支撑测评未完成。")]


def _claim_relationships(value: Any) -> list[dict[str, Any]]:
    fields = ("relationship_id", "ref", "url", "claim", "status", "confidence", "reason", "matched_terms", "matched_numbers", "source_readable")
    output: list[dict[str, Any]] = []
    for item in _safe_list(value):
        safe = _public_item(item, fields, ("source_readable",))
        if isinstance(safe, dict):
            status = _safe_string(item.get("status")) if isinstance(item, dict) else ""
            reasons = {
                "supported": "来源充分支撑该断言。",
                "partially_supported": "来源仅部分支撑该断言。",
                "unsupported": "未找到充分来源支撑。",
                "unchecked": "该断言尚未完成核验。",
            }
            if status in reasons:
                safe["reason"] = reasons[status]
            output.append(safe)
    return output


def _claim_support_summary(value: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if value is None:
        return _not_evaluated_claim_support(), []
    source = _input_dict(value)
    if source is None or source.get("evaluation_error") is not None:
        return _failed_claim_support()
    relationship_count, count_valid = _count(source.get("relationship_count"), missing_is_valid=True)
    if not count_valid or relationship_count is None:
        return _failed_claim_support()
    if relationship_count == 0:
        zero_values = (source.get("supported_count"), source.get("partially_supported_count"), source.get("unsupported_count"), source.get("unchecked_count"))
        for item in zero_values:
            count, valid = _count(item, missing_is_valid=True)
            if not valid or count != 0:
                return _failed_claim_support()
        return ({"status": "no_public_relationships", "threshold": PUBLIC_LINK_SUPPORT_THRESHOLD,
                 "relationship_count": 0, "supported_count": 0, "partially_supported_count": 0,
                 "unsupported_count": 0, "unchecked_count": 0, "accuracy": None,
                 "requirement_met": None, "relationships": []}, [])
    counts: dict[str, int] = {}
    for key in _SUPPORT_FIELDS:
        count, valid = _count(source.get(key))
        if not valid or count is None:
            return _failed_claim_support()
        counts[key] = count
    if sum(counts[key] for key in _SUPPORT_FIELDS[1:]) != counts["relationship_count"]:
        return _failed_claim_support()
    relationships = _claim_relationships(source.get("relationships"))
    if not isinstance(source.get("relationships"), list) or len(relationships) != counts["relationship_count"]:
        return _failed_claim_support()
    accuracy = round(counts["supported_count"] / counts["relationship_count"], 4)
    return ({"status": "completed", "threshold": PUBLIC_LINK_SUPPORT_THRESHOLD, **counts,
             "accuracy": accuracy, "requirement_met": accuracy >= PUBLIC_LINK_SUPPORT_THRESHOLD,
             "relationships": relationships}, [])


def _style_cleanup_summary(value: Any) -> tuple[dict[str, int], list[dict[str, str]]]:
    source = _input_dict(value)
    try:
        count, valid = _count(source.get("removed_count"), missing_is_valid=True) if source else (0, True)
        return {"removed_count": count if valid and count is not None else 0}, []
    except Exception:
        return {"removed_count": 0}, [_error("style_cleanup", "evaluation_failed", "格式清理汇总未完成。")]


def _normalized_report_path(raw: Any, extension: str) -> str:
    if not isinstance(raw, str) or "\\" in raw:
        return ""
    candidate = raw
    try:
        for _ in range(4):
            decoded = unquote(candidate)
            if decoded == candidate:
                break
            candidate = decoded
        else:
            if unquote(candidate) != candidate:
                return ""
    except Exception:
        return ""
    if "%" in candidate or any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        return ""
    prefix = "/outputs/evaluations/"
    if not candidate.startswith(prefix) or "?" in candidate or "#" in candidate:
        return ""
    filename = candidate[len(prefix):]
    if not filename or "/" in filename or filename in {".", ".."} or not filename.endswith(extension):
        return ""
    return candidate


def _report_paths_summary(value: Any) -> tuple[dict[str, str], list[dict[str, str]]]:
    paths = {"markdown": "", "word": "", "pdf": ""}
    source = _input_dict(value)
    if source is None:
        return paths, []
    errors: list[dict[str, str]] = []
    for key, extension in (("markdown", ".md"), ("word", ".docx"), ("pdf", ".pdf")):
        raw = source.get(key)
        if raw in (None, ""):
            continue
        path = _normalized_report_path(raw, extension)
        if path:
            paths[key] = path
        else:
            errors.append(_error("evaluation_report_paths", "invalid_path", "测评报告路径无效。"))
    return paths, errors


def _public_links(accessibility: dict[str, Any], claim_support: dict[str, Any]) -> dict[str, Any]:
    details = {"accessibility": [dict(item) for item in accessibility["results"] if isinstance(item, dict)],
               "claim_support": [dict(item) for item in claim_support["relationships"] if isinstance(item, dict)]}
    return {
        "accessibility": accessibility, "claim_support": claim_support, "details": details,
        # Deprecated flat aliases retained until the existing evaluation panel migrates.
        "status": accessibility["status"], "threshold": accessibility["threshold"],
        "total_count": accessibility["total_count"], "checked_count": accessibility["checked_count"],
        "accessible_count": accessibility["accessible_count"], "inaccessible_count": accessibility["inaccessible_count"],
        "accessibility_rate": accessibility["rate"], "requirement_met": accessibility["requirement_met"],
    }


def build_evaluation_summary(entity_eval: Any = None, url_check: Any = None, url_source_eval: Any = None,
                             style_cleanup: Any = None, evaluation_report_paths: Any = None) -> dict[str, Any]:
    """Combine existing evaluation results only; never rerun or expose raw diagnostics."""
    try:
        entity, entity_errors = _entity_summary(entity_eval)
    except Exception:
        entity, entity_errors = _failed_entity(), [_error("entity", "evaluation_failed", "实体抽取测评未完成。")]
    try:
        accessibility, access_errors = _accessibility_summary(url_check)
    except Exception:
        accessibility, access_errors = _failed_accessibility()
    try:
        claim_support, support_errors = _claim_support_summary(url_source_eval)
    except Exception:
        claim_support, support_errors = _failed_claim_support()
    try:
        cleanup, cleanup_errors = _style_cleanup_summary(style_cleanup)
    except Exception:
        cleanup, cleanup_errors = {"removed_count": 0}, [_error("style_cleanup", "evaluation_failed", "格式清理汇总未完成。")]
    try:
        paths, path_errors = _report_paths_summary(evaluation_report_paths)
    except Exception:
        paths, path_errors = {"markdown": "", "word": "", "pdf": ""}, [_error("evaluation_report_paths", "invalid_path", "测评报告路径无效。")]
    errors = entity_errors + access_errors + support_errors + cleanup_errors + path_errors
    participating = tuple(status for status in (entity["status"], accessibility["status"], claim_support["status"])
                          if status != "not_evaluated")
    failed = {"evaluation_failed", "invalid_ground_truth"}
    status = "completed" if not errors else ("failed" if participating and all(item in failed for item in participating) else "partial")
    return {"status": status, "entity": entity, "public_links": _public_links(accessibility, claim_support),
            "style_cleanup": cleanup, "evaluation_report_paths": paths, "errors": errors}
