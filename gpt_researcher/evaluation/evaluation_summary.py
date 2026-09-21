"""Build the stable, user-facing report evaluation payload."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple


ENTITY_THRESHOLD = 0.90
PUBLIC_LINK_THRESHOLD = 0.98


def _safe_file_name(value: Any) -> str:
    return Path(str(value or "")).name or "标准答案文件"


def _entity_summary(
    entity_eval: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    mode = str(entity_eval.get("mode") or "proxy")
    source_status = str(entity_eval.get("status") or "evaluation_failed")
    ground_truth_file = _safe_file_name(entity_eval.get("ground_truth_path"))
    errors: List[Dict[str, str]] = []
    overall = None
    categories: Dict[str, Any] = {}
    proxy_rate = None
    status = source_status
    message = str(entity_eval.get("note") or "")

    metrics = entity_eval.get("metrics")
    if mode == "strict" and isinstance(metrics, dict):
        overall_source = metrics.get("overall")
        if isinstance(overall_source, dict):
            overall = dict(overall_source)
            f1 = overall.get("f1")
            overall["requirement_met"] = (
                isinstance(f1, (int, float)) and not isinstance(f1, bool) and f1 >= ENTITY_THRESHOLD
            )
            source_categories = metrics.get("categories")
            categories = dict(source_categories) if isinstance(source_categories, dict) else {}
            status = "completed"
            message = "已根据任务金标准计算严格实体指标。"
        else:
            status = "evaluation_failed"
    elif mode == "invalid":
        status = "invalid_ground_truth"
        reason = str(entity_eval.get("ground_truth_message") or "标准答案无效。")
        errors.append(
            {
                "scope": "entity",
                "code": str(entity_eval.get("ground_truth_error_code") or "invalid_ground_truth"),
                "message": f"{ground_truth_file}：{reason}",
            }
        )
    elif mode == "strict":
        status = "evaluation_failed"
    else:
        auto_evidence = entity_eval.get("auto_evidence_eval")
        if isinstance(auto_evidence, dict):
            proxy_rate = auto_evidence.get("auto_evidence_accuracy")

    if status == "evaluation_failed" and not errors:
        errors.append(
            {
                "scope": "entity",
                "code": "evaluation_failed",
                "message": "实体抽取测评未完成。",
            }
        )

    return (
        {
            "mode": mode,
            "status": status,
            "ground_truth_path": "" if ground_truth_file == "标准答案文件" else ground_truth_file,
            "threshold": ENTITY_THRESHOLD,
            "overall": overall,
            "categories": categories,
            "proxy_evidence_support_rate": proxy_rate if mode != "strict" else None,
            "message": message,
        },
        errors,
    )


def _link_summary(
    url_check: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    has_error = bool(url_check.get("evaluation_error"))
    total_count = int(url_check.get("total_urls") or 0)
    checked_count = int(url_check.get("checked_urls") or 0)
    accessible_count = int(url_check.get("accessible_urls") or 0)
    inaccessible_count = int(url_check.get("failed_urls") or 0)

    if has_error:
        status = "evaluation_failed"
        rate = None
        errors.append(
            {
                "scope": "public_links",
                "code": "evaluation_failed",
                "message": "公开链接可访问性测评未完成。",
            }
        )
    elif total_count == 0:
        status = "no_public_urls"
        rate = None
    else:
        status = "completed"
        rate = url_check.get("accessibility_rate")

    requirement_met = None
    if status == "completed" and isinstance(rate, (int, float)) and not isinstance(rate, bool):
        requirement_met = rate >= PUBLIC_LINK_THRESHOLD

    return (
        {
            "status": status,
            "threshold": PUBLIC_LINK_THRESHOLD,
            "total_count": total_count,
            "checked_count": checked_count,
            "accessible_count": accessible_count,
            "inaccessible_count": inaccessible_count,
            "accessibility_rate": rate,
            "requirement_met": requirement_met,
        },
        errors,
    )


def build_evaluation_summary(
    entity_eval: Dict[str, Any] | None,
    url_check: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Combine raw entity and URL results without rerunning either evaluation."""
    entity, entity_errors = _entity_summary(entity_eval or {})
    public_links, link_errors = _link_summary(url_check or {})
    errors = entity_errors + link_errors
    successful_parts = sum(
        (
            entity["status"] not in {"invalid_ground_truth", "evaluation_failed"},
            public_links["status"] != "evaluation_failed",
        )
    )
    status = "completed" if not errors else ("partial" if successful_parts else "failed")
    return {
        "status": status,
        "entity": entity,
        "public_links": public_links,
        "errors": errors,
    }
