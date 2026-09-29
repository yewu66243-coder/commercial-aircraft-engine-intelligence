"""Unified deterministic evaluation for an already-saved, cleaned report."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from pathlib import Path
from typing import Any

from backend.reporting.evaluation_report import (
    export_evaluation_report,
    render_evaluation_report_markdown,
)
from gpt_researcher.evaluation.entity_evaluator import evaluate_report_entities
from gpt_researcher.evaluation.evaluation_summary import build_evaluation_summary
from gpt_researcher.evaluation.ground_truth_io import ground_truth_path_for_task
from gpt_researcher.evaluation.link_accessibility import (
    evaluate_link_accessibility,
    extract_public_urls,
)
from gpt_researcher.evaluation.records import EvaluationRecordStore
from gpt_researcher.evaluation.source_evaluator import evaluate_public_url_sources


LOGGER = logging.getLogger(__name__)


class SavedReportUnavailableError(ValueError):
    """Raised when reevaluation cannot use a persisted final report body."""


def _default_ground_truth_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "outputs" / "records" / "entity_ground_truths"


def resolve_active_ground_truth_path(
    task: str, directory: str | Path | None = None
) -> Path | None:
    path = ground_truth_path_for_task(task, directory or _default_ground_truth_dir())
    return path if path.is_file() else None


def _failed_entity(error: BaseException) -> dict[str, Any]:
    return {
        "status": "evaluation_failed",
        "mode": "proxy",
        "metrics": None,
        "ground_truth_path": "",
        "ground_truth_error_code": "",
        "ground_truth_message": "",
        "extracted_count": 0,
        "evidence_supported_count": 0,
        "auto_evidence_eval": {},
        "note": "实体抽取测评未完成。",
        "evaluation_error": type(error).__name__,
    }


def _failed_accessibility(report: str, error: BaseException) -> dict[str, Any]:
    try:
        total = len(extract_public_urls(report))
    except Exception:
        total = 0
    return {
        "total_urls": total,
        "checked_urls": 0,
        "accessible_urls": 0,
        "failed_urls": 0,
        "accessibility_rate": None,
        "skipped_urls": total,
        "ssl_unverified_accessible_urls": 0,
        "failure_reasons": {},
        "results": [],
        "evaluation_error": type(error).__name__,
    }


def _failed_source(error: BaseException) -> dict[str, Any]:
    return {
        "relationship_count": 0,
        "supported_count": 0,
        "partially_supported_count": 0,
        "unsupported_count": 0,
        "unchecked_count": 0,
        "support_accuracy": None,
        "relationships": [],
        "evaluation_error": type(error).__name__,
    }


async def _evaluate_accessibility(report: str) -> dict[str, Any]:
    try:
        return await evaluate_link_accessibility(report)
    except Exception as error:
        LOGGER.exception("URL accessibility evaluation failed")
        return _failed_accessibility(report, error)


async def _evaluate_sources(report: str) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(evaluate_public_url_sources, report)
    except Exception as error:
        LOGGER.exception("Public URL source evaluation failed")
        return _failed_source(error)


async def _evaluate_entities(
    report: str, task: str, ground_truth_path: str | Path | None
) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            evaluate_report_entities, report, task, ground_truth_path=ground_truth_path
        )
    except Exception as error:
        LOGGER.exception("Entity evaluation failed")
        return _failed_entity(error)


async def evaluate_saved_report(
    *,
    task: str,
    run_id: str,
    report: str,
    style_cleanup: dict[str, object] | None,
    ground_truth_path: str | Path | None,
    output_dir: str | Path = "outputs/evaluations",
) -> dict[str, object]:
    """Evaluate the supplied text as-is; this function never edits or regenerates it."""
    if not isinstance(report, str) or not report.strip():
        raise SavedReportUnavailableError("已保存的清理后报告正文不可用。")

    # ``ground_truth_path`` is the caller-resolved active file and is passed
    # through unchanged: a path scores strictly against that file, ``None`` keeps
    # entity metrics in proxy mode instead of silently using another file.
    evaluated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    url_check, url_source_eval, entity_eval = await asyncio.gather(
        _evaluate_accessibility(report),
        _evaluate_sources(report),
        _evaluate_entities(report, task, ground_truth_path),
    )

    initial_summary = build_evaluation_summary(
        entity_eval,
        url_check,
        url_source_eval,
        style_cleanup=style_cleanup,
    )
    paths: dict[str, Any] = {"markdown": "", "word": "", "pdf": "", "errors": []}
    try:
        markdown = render_evaluation_report_markdown(
            task=task,
            run_id=run_id,
            evaluated_at=evaluated_at,
            summary=initial_summary,
            entity_eval=entity_eval,
            url_check=url_check,
            url_source_eval=url_source_eval,
        )
        exported = await export_evaluation_report(
            markdown=markdown,
            task=task,
            run_id=run_id,
            evaluated_at=evaluated_at,
            output_dir=output_dir,
        )
        if isinstance(exported, dict):
            paths.update(exported)
    except Exception as error:
        LOGGER.exception("Standalone evaluation report export failed")
        paths["errors"] = [
            {"format": "markdown", "code": "export_failed", "message": "测评报告导出失败。"}
        ]

    final_summary = build_evaluation_summary(
        entity_eval,
        url_check,
        url_source_eval,
        style_cleanup=style_cleanup,
        evaluation_report_paths=paths,
    )
    return {
        "evaluated_at": evaluated_at,
        "entity_eval": entity_eval,
        "url_check": url_check,
        "public_url_source_eval": url_source_eval,
        "evaluation_summary": final_summary,
        "evaluation_report_paths": dict(final_summary["evaluation_report_paths"]),
        "evaluation_report_errors": paths.get("errors", []) if isinstance(paths.get("errors"), list) else [],
    }


async def reevaluate_saved_run(
    *,
    store: EvaluationRecordStore,
    run_id: str,
    ground_truth_path: str | Path | None,
    output_dir: str | Path = "outputs/evaluations",
) -> dict[str, object]:
    """Reevaluate one stored report and append, never replace, its history."""
    record = store.get_run(run_id)
    if record is None:
        raise KeyError(run_id)
    report = record.get("evidence_report")
    if not isinstance(report, str) or not report.strip():
        raise SavedReportUnavailableError("已保存的清理后报告正文不可用。")
    task = record.get("task")
    if not isinstance(task, str) or not task.strip():
        raise SavedReportUnavailableError("已保存的报告任务信息不可用。")
    style_cleanup = record.get("report_style_cleanup")
    if not isinstance(style_cleanup, dict):
        style_cleanup = record.get("evaluation_summary", {}).get("style_cleanup")
    if not isinstance(style_cleanup, dict):
        style_cleanup = None
    result = await evaluate_saved_report(
        task=task,
        run_id=run_id,
        report=report,
        style_cleanup=style_cleanup,
        ground_truth_path=ground_truth_path,
        output_dir=output_dir,
    )
    store.append_reevaluation(run_id, result)
    return result

