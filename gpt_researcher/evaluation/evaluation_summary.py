"""Build the stable, user-facing report evaluation payload."""

from __future__ import annotations

import math
from pathlib import PurePath
from typing import Any
from urllib.parse import unquote


ENTITY_THRESHOLD = 0.90
PUBLIC_LINK_ACCESSIBILITY_THRESHOLD = 0.98
PUBLIC_LINK_SUPPORT_THRESHOLD = 0.90
# Kept for consumers of the previous single-link summary API.
PUBLIC_LINK_THRESHOLD = PUBLIC_LINK_ACCESSIBILITY_THRESHOLD


def _error(scope: str, code: str, message: str) -> dict[str, str]:
    """Return only fixed, presentation-safe error fields."""
    return {"scope": scope, "code": code, "message": message}


def _input_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _safe_file_name(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    # PurePath on a POSIX host does not treat Windows separators as separators.
    file_name = PurePath(value.replace("\\", "/")).name
    return "" if file_name in {".", ".."} else file_name


def _rate(value: Any) -> tuple[float | None, bool]:
    """Return an optional finite unit-interval rate and its validity."""
    if value is None:
        return None, True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, False
    try:
        numeric = float(value)
    except OverflowError:
        return None, False
    if not math.isfinite(numeric) or not 0 <= numeric <= 1:
        return None, False
    return numeric, True


def _count(value: Any) -> tuple[int, bool]:
    """Accept only non-negative integer counts; missing counts default to zero."""
    if value is None:
        return 0, True
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0, False
    return value, True


def _detail_list(value: Any) -> tuple[list[Any], bool]:
    if value is None:
        return [], True
    if not isinstance(value, list):
        return [], False
    return list(value), True


def _metric_values(value: Any) -> tuple[dict[str, int], dict[str, float | None], bool]:
    """Validate the shared count/rate contract used by strict metrics."""
    if not isinstance(value, dict):
        return {}, {}, False
    counts: dict[str, int] = {}
    rates: dict[str, float | None] = {}
    for key in ("true_positive", "false_positive", "false_negative"):
        count, valid = _count(value.get(key))
        if not valid:
            return {}, {}, False
        counts[key] = count
    for key in ("precision", "recall", "f1"):
        rate, valid = _rate(value.get(key))
        if not valid:
            return {}, {}, False
        rates[key] = rate
    return counts, rates, True


def _safe_display_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    try:
        return str(value)
    except (OverflowError, ValueError):
        return ""


def _validated_categories(value: Any) -> tuple[dict[Any, dict[str, Any]], bool]:
    """Copy categories only after applying the same metric validation as overall."""
    if value is None:
        return {}, True
    if not isinstance(value, dict):
        return {}, False
    categories: dict[Any, dict[str, Any]] = {}
    for category, metrics in value.items():
        _, _, valid = _metric_values(metrics)
        if not valid:
            return {}, False
        copied = dict(metrics)
        if "label" in copied:
            copied["label"] = _safe_display_string(copied["label"])
        categories[category] = copied
    return categories, True


def _entity_details(source: dict[str, Any], metrics: dict[str, Any]) -> tuple[list[Any], list[Any], list[Any]]:
    """Use the evaluator's public names, with metrics as a stable fallback."""
    def get_list(*names: str) -> list[Any]:
        for container in (source, metrics):
            for name in names:
                value = container.get(name)
                if isinstance(value, list):
                    return list(value)
        return []

    return (
        get_list("matched", "matches", "correct_entities"),
        get_list("false_positives", "wrong_entities"),
        get_list("false_negatives", "missed_entities"),
    )


def _entity_summary(value: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    source = _input_dict(value)
    if source is None:
        return _failed_entity(), [_error("entity", "evaluation_failed", "实体抽取测评未完成。")]

    raw_mode = source.get("mode")
    mode = raw_mode if isinstance(raw_mode, str) and raw_mode in {"strict", "proxy", "invalid"} else "proxy"
    metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
    matched, false_positives, false_negatives = _entity_details(source, metrics)
    base = {
        "mode": mode,
        "status": "completed" if mode == "strict" else "proxy",
        "ground_truth_path": _safe_file_name(source.get("ground_truth_path")),
        "threshold": ENTITY_THRESHOLD,
        "overall": None,
        "categories": {},
        "matched": matched,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "proxy_evidence_support_rate": None,
        "message": "",
    }

    if source.get("status") == "evaluation_failed":
        base.update({"status": "evaluation_failed", "message": "实体抽取测评未完成。"})
        return base, [_error("entity", "evaluation_failed", "实体抽取测评未完成。")]

    if mode == "invalid":
        base["status"] = "invalid_ground_truth"
        base["message"] = "标准答案无效，未执行严格实体评估。"
        return base, [_error("entity", "invalid_ground_truth", "标准答案文件无效。")]

    if mode == "proxy":
        auto_evidence = source.get("auto_evidence_eval")
        proxy_rate, valid = _rate(auto_evidence.get("auto_evidence_accuracy") if isinstance(auto_evidence, dict) else None)
        if not valid:
            base["status"] = "evaluation_failed"
            base["message"] = "实体抽取测评未完成。"
            return base, [_error("entity", "evaluation_failed", "实体抽取测评未完成。")]
        base["proxy_evidence_support_rate"] = proxy_rate
        base["message"] = "已完成自动证据核验，严格实体指标仍需标准答案。"
        return base, []

    counts, rates, valid_overall = _metric_values(metrics.get("overall"))
    categories, valid_categories = _validated_categories(metrics.get("categories"))
    if not valid_overall or not valid_categories:
        base.update({"status": "evaluation_failed", "message": "实体抽取测评未完成。"})
        return base, [_error("entity", "evaluation_failed", "实体抽取测评未完成。")]

    base["categories"] = categories
    base["overall"] = {
        **counts,
        **rates,
        "requirement_met": None if rates["f1"] is None else rates["f1"] >= ENTITY_THRESHOLD,
    }
    base["message"] = "已根据任务金标准计算严格实体指标。"
    return base, []


def _failed_entity() -> dict[str, Any]:
    return {
        "mode": "proxy",
        "status": "evaluation_failed",
        "ground_truth_path": "",
        "threshold": ENTITY_THRESHOLD,
        "overall": None,
        "categories": {},
        "matched": [],
        "false_positives": [],
        "false_negatives": [],
        "proxy_evidence_support_rate": None,
        "message": "实体抽取测评未完成。",
    }


def _failed_accessibility() -> tuple[dict[str, Any], list[dict[str, str]]]:
    return (
        {
            "status": "evaluation_failed",
            "threshold": PUBLIC_LINK_ACCESSIBILITY_THRESHOLD,
            "total_count": 0,
            "checked_count": 0,
            "accessible_count": 0,
            "inaccessible_count": 0,
            "rate": None,
            "requirement_met": None,
            "results": [],
        },
        [_error("public_links", "evaluation_failed", "公开链接可访问性测评未完成。")],
    )


def _accessibility_summary(value: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    source = _input_dict(value)
    if source is None:
        return _failed_accessibility()
    if source.get("evaluation_error"):
        return _failed_accessibility()

    fields = {
        "total_count": "total_urls",
        "checked_count": "checked_urls",
        "accessible_count": "accessible_urls",
        "inaccessible_count": "failed_urls",
    }
    counts: dict[str, int] = {}
    for target, raw_key in fields.items():
        count, valid = _count(source.get(raw_key))
        if not valid:
            return _failed_accessibility()
        counts[target] = count
    if counts["total_count"] == 0:
        return (
            {
                "status": "no_public_urls",
                "threshold": PUBLIC_LINK_ACCESSIBILITY_THRESHOLD,
                **counts,
                "rate": None,
                "requirement_met": None,
                "results": [],
            },
            [],
        )
    results, valid_results = _detail_list(source.get("results"))
    rate, valid_rate = _rate(source.get("accessibility_rate"))
    if not valid_results or not valid_rate:
        return _failed_accessibility()
    status = "completed"
    requirement_met = None if rate is None else rate >= PUBLIC_LINK_ACCESSIBILITY_THRESHOLD
    return (
        {
            "status": status,
            "threshold": PUBLIC_LINK_ACCESSIBILITY_THRESHOLD,
            **counts,
            "rate": rate,
            "requirement_met": requirement_met,
            "results": results,
        },
        [],
    )


def _not_evaluated_claim_support() -> dict[str, Any]:
    return {
        "status": "not_evaluated",
        "threshold": PUBLIC_LINK_SUPPORT_THRESHOLD,
        "relationship_count": 0,
        "supported_count": 0,
        "partially_supported_count": 0,
        "unsupported_count": 0,
        "unchecked_count": 0,
        "accuracy": None,
        "requirement_met": None,
        "relationships": [],
    }


def _failed_claim_support() -> tuple[dict[str, Any], list[dict[str, str]]]:
    result = _not_evaluated_claim_support()
    result["status"] = "evaluation_failed"
    return result, [_error("public_links", "evaluation_failed", "断言—链接支撑测评未完成。")]


def _claim_support_summary(value: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if value is None:
        return _not_evaluated_claim_support(), []
    source = _input_dict(value)
    if source is None or source.get("evaluation_error"):
        return _failed_claim_support()

    fields = (
        "relationship_count",
        "supported_count",
        "partially_supported_count",
        "unsupported_count",
        "unchecked_count",
    )
    counts: dict[str, int] = {}
    for key in fields:
        count, valid = _count(source.get(key))
        if not valid:
            return _failed_claim_support()
        counts[key] = count
    if counts["relationship_count"] == 0:
        return (
            {
                "status": "no_public_relationships",
                "threshold": PUBLIC_LINK_SUPPORT_THRESHOLD,
                **counts,
                "accuracy": None,
                "requirement_met": None,
                "relationships": [],
            },
            [],
        )
    relationships, valid_relationships = _detail_list(source.get("relationships"))
    accuracy, valid_accuracy = _rate(source.get("support_accuracy"))
    if not valid_relationships or not valid_accuracy:
        return _failed_claim_support()
    status = "completed"
    requirement_met = None if accuracy is None else accuracy >= PUBLIC_LINK_SUPPORT_THRESHOLD
    return (
        {
            "status": status,
            "threshold": PUBLIC_LINK_SUPPORT_THRESHOLD,
            **counts,
            "accuracy": accuracy,
            "requirement_met": requirement_met,
            "relationships": relationships,
        },
        [],
    )


def _style_cleanup_summary(value: Any) -> dict[str, int]:
    source = _input_dict(value)
    count, valid = _count(source.get("removed_count") if source is not None else None)
    return {"removed_count": count if valid else 0}


def _report_paths_summary(value: Any) -> dict[str, str]:
    source = _input_dict(value)
    paths = {"markdown": "", "word": "", "pdf": ""}
    if source is None:
        return paths
    prefix = "/outputs/evaluations/"
    for key in paths:
        raw = source.get(key)
        if not isinstance(raw, str):
            continue
        candidate = unquote(raw).replace("\\", "/")
        if candidate.startswith("outputs/evaluations/"):
            candidate = f"/{candidate}"
        if (
            candidate.startswith(prefix)
            and ".." not in candidate.split("/")
            and "?" not in candidate
            and "#" not in candidate
        ):
            paths[key] = candidate
    return paths


def build_evaluation_summary(
    entity_eval: Any = None,
    url_check: Any = None,
    url_source_eval: Any = None,
    style_cleanup: Any = None,
    evaluation_report_paths: Any = None,
) -> dict[str, Any]:
    """Combine completed evaluation data without rerunning evaluation work."""
    entity, entity_errors = _entity_summary(entity_eval)
    accessibility, accessibility_errors = _accessibility_summary(url_check)
    claim_support, support_errors = _claim_support_summary(url_source_eval)
    errors = entity_errors + accessibility_errors + support_errors
    components = (entity["status"], accessibility["status"], claim_support["status"])
    participating_components = tuple(status for status in components if status != "not_evaluated")
    failed = {"evaluation_failed", "invalid_ground_truth"}
    if not errors:
        status = "completed"
    elif participating_components and all(component in failed for component in participating_components):
        status = "failed"
    else:
        status = "partial"

    return {
        "status": status,
        "entity": entity,
        "public_links": {
            "accessibility": accessibility,
            "claim_support": claim_support,
            "details": {
                "accessibility": accessibility["results"],
                "claim_support": claim_support["relationships"],
            },
        },
        "style_cleanup": _style_cleanup_summary(style_cleanup),
        "evaluation_report_paths": _report_paths_summary(evaluation_report_paths),
        "errors": errors,
    }
