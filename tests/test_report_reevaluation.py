from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

import json

from gpt_researcher.evaluation import entity_evaluator, report_evaluation
from gpt_researcher.evaluation.records import EvaluationRecordStore


REPORT = "# 已清理报告\n\n确定事实。[URL1]\n\n- [URL1] https://example.test/source"

ENTITY_REPORT = (
    "# 已清理报告\n\n"
    "## 实体与参数清单\n"
    "| 类别 | 实体/参数 | 数值/描述 | 证据 |\n"
    "| --- | --- | --- | --- |\n"
    "| 型号 | 指定实体 | engine | - |\n"
)

LOCAL_EVIDENCE_REPORT = (
    "# 已清理报告\n\n"
    "## 实体与参数清单\n"
    "| 类别 | 实体/参数 | 数值/描述 | 证据 |\n"
    "| --- | --- | --- | --- |\n"
    "| 型号 | PW1000G | engine | spec.pdf |\n"
)


def _write_truth(path: Path, name: str, task: str = "GTF") -> None:
    path.write_text(
        json.dumps(
            {"task": task, "entities": [{"name": name, "type": "型号"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _raw_results():
    entity = {
        "status": "auto_evaluated",
        "mode": "strict",
        "metrics": {
            "overall": {"true_positive": 1, "false_positive": 0, "false_negative": 0,
                        "precision": 1.0, "recall": 1.0, "f1": 1.0},
            "categories": {}, "matches": [], "correct_entities": [],
            "wrong_entities": [], "missed_entities": [],
        },
        "ground_truth_path": "truth.json",
    }
    urls = {
        "total_urls": 1, "checked_urls": 1, "accessible_urls": 1,
        "failed_urls": 0, "skipped_urls": 0, "accessibility_rate": 1.0,
        "results": [{"url": "https://example.test/source", "accessible": True,
                     "status_code": 200, "failure_reason": "", "method": "HEAD"}],
    }
    support = {
        "relationship_count": 1, "supported_count": 1,
        "partially_supported_count": 0, "unsupported_count": 0,
        "unchecked_count": 0, "support_accuracy": 1.0,
        "relationships": [{"relationship_id": "R1", "url": "https://example.test/source",
                           "claim": "确定事实。", "status": "supported", "confidence": 1.0,
                           "source_readable": True}],
    }
    return entity, urls, support


def test_evaluate_saved_report_uses_exact_saved_text_and_adds_export_paths(tmp_path):
    entity, urls, support = _raw_results()
    url_evaluator = AsyncMock(return_value=urls)
    source_evaluator = Mock(return_value=support)
    entity_evaluator = Mock(return_value=entity)
    renderer = Mock(return_value="# 独立测评报告")
    exporter = AsyncMock(return_value={
        "markdown": "/outputs/evaluations/eval.md",
        "word": "/outputs/evaluations/eval.docx",
        "pdf": "/outputs/evaluations/eval.pdf",
        "errors": [],
    })

    with (
        patch.object(report_evaluation, "evaluate_link_accessibility", url_evaluator),
        patch.object(report_evaluation, "evaluate_public_url_sources", source_evaluator),
        patch.object(report_evaluation, "evaluate_report_entities", entity_evaluator),
        patch.object(report_evaluation, "render_evaluation_report_markdown", renderer),
        patch.object(report_evaluation, "export_evaluation_report", exporter),
    ):
        result = asyncio.run(report_evaluation.evaluate_saved_report(
            task="GTF", run_id="run-1", report=REPORT,
            style_cleanup={"removed_count": 2}, ground_truth_path=tmp_path / "truth.json",
            output_dir=tmp_path,
        ))

    url_evaluator.assert_awaited_once_with(REPORT)
    source_evaluator.assert_called_once_with(REPORT)
    assert entity_evaluator.call_args.args == (REPORT, "GTF", ())
    assert entity_evaluator.call_args.kwargs["ground_truth_path"] == tmp_path / "truth.json"
    assert renderer.call_args.kwargs["summary"]["evaluation_report_paths"] == {
        "markdown": "", "word": "", "pdf": ""
    }
    assert exporter.call_args.kwargs["markdown"] == "# 独立测评报告"
    assert exporter.call_args.kwargs["output_dir"] == tmp_path
    assert result["entity_eval"] == entity
    assert result["url_check"] == urls
    assert result["public_url_source_eval"] == support
    assert result["evaluation_summary"]["style_cleanup"] == {"removed_count": 2}
    assert result["evaluation_summary"]["evaluation_report_paths"]["word"].endswith("eval.docx")
    assert datetime.fromisoformat(result["evaluated_at"])


def test_three_evaluators_fail_independently_and_still_produce_a_summary(tmp_path):
    _, urls, _ = _raw_results()

    with (
        patch.object(report_evaluation, "evaluate_link_accessibility", AsyncMock(return_value=urls)),
        patch.object(report_evaluation, "evaluate_public_url_sources", side_effect=RuntimeError("private source")),
        patch.object(report_evaluation, "evaluate_report_entities", side_effect=ValueError("private entity")),
        patch.object(report_evaluation, "render_evaluation_report_markdown", return_value="# partial"),
        patch.object(report_evaluation, "export_evaluation_report", new=AsyncMock(return_value={
            "markdown": "/outputs/evaluations/partial.md", "word": "", "pdf": "", "errors": []
        })),
    ):
        result = asyncio.run(report_evaluation.evaluate_saved_report(
            task="GTF", run_id="run-partial", report=REPORT,
            style_cleanup=None, ground_truth_path=None, output_dir=tmp_path,
        ))

    assert result["url_check"] == urls
    assert result["entity_eval"]["status"] == "evaluation_failed"
    assert result["public_url_source_eval"]["evaluation_error"] == "RuntimeError"
    assert result["evaluation_summary"]["status"] == "partial"
    assert "private" not in repr(result)


def test_reevaluate_saved_run_appends_history_without_generating_a_report(tmp_path):
    store = EvaluationRecordStore(tmp_path / "records.json")
    store.append_run({
        "run_id": "run-1", "task": "GTF", "evidence_report": REPORT,
        "report_style_cleanup": {"removed_count": 4},
        "evaluation_summary": {"status": "completed"},
    })
    fresh = {
        "evaluated_at": "2026-09-29T12:00:00+08:00",
        "entity_eval": {}, "url_check": {}, "public_url_source_eval": {},
        "evaluation_summary": {"status": "partial"},
        "evaluation_report_paths": {"markdown": "/outputs/evaluations/new.md", "word": "", "pdf": ""},
    }

    with patch.object(report_evaluation, "evaluate_saved_report", new=AsyncMock(return_value=fresh)) as evaluator:
        result = asyncio.run(report_evaluation.reevaluate_saved_run(
            store=store, run_id="run-1", ground_truth_path=tmp_path / "new-truth.json",
            output_dir=tmp_path,
        ))

    evaluator.assert_awaited_once_with(
        task="GTF", run_id="run-1", report=REPORT,
        style_cleanup={"removed_count": 4},
        ground_truth_path=tmp_path / "new-truth.json",
        selected_sources=[], output_dir=tmp_path,
    )
    assert result == fresh
    saved = store.get_run("run-1")
    assert saved["evaluation_summary"] == {"status": "completed"}
    assert saved["reevaluations"] == [fresh]


@pytest.mark.parametrize("evidence_report", [None, "", "   "])
def test_reevaluate_saved_run_rejects_missing_saved_report(tmp_path, evidence_report):
    store = EvaluationRecordStore(tmp_path / "records.json")
    store.append_run({"run_id": "run-1", "task": "GTF", "evidence_report": evidence_report})

    with pytest.raises(report_evaluation.SavedReportUnavailableError):
        asyncio.run(report_evaluation.reevaluate_saved_run(
            store=store, run_id="run-1", ground_truth_path=None, output_dir=tmp_path,
        ))


def test_resolve_active_ground_truth_path_uses_hashed_task_name(tmp_path):
    expected = report_evaluation.ground_truth_path_for_task("GTF / unsafe", tmp_path)
    assert report_evaluation.resolve_active_ground_truth_path("GTF / unsafe", tmp_path) is None
    expected.write_text("{}", encoding="utf-8")

    assert report_evaluation.resolve_active_ground_truth_path("GTF / unsafe", tmp_path) == expected


def _run_orchestration(report: str, ground_truth_path, output_dir: Path, monkeypatch, default_dir: Path):
    _, urls, support = _raw_results()
    monkeypatch.setattr(entity_evaluator, "get_ground_truth_dir", lambda: default_dir)
    with (
        patch.object(report_evaluation, "evaluate_link_accessibility", AsyncMock(return_value=urls)),
        patch.object(report_evaluation, "evaluate_public_url_sources", Mock(return_value=support)),
        patch.object(report_evaluation, "render_evaluation_report_markdown", Mock(return_value="# 测评")),
        patch.object(report_evaluation, "export_evaluation_report", new=AsyncMock(return_value={
            "markdown": "/outputs/evaluations/eval.md",
            "word": "/outputs/evaluations/eval.docx",
            "pdf": "/outputs/evaluations/eval.pdf",
            "errors": [],
        })),
    ):
        return asyncio.run(report_evaluation.evaluate_saved_report(
            task="GTF", run_id="run-gt", report=report,
            style_cleanup=None, ground_truth_path=ground_truth_path, output_dir=output_dir,
        ))


def test_evaluate_saved_report_scores_with_the_specified_ground_truth(tmp_path, monkeypatch):
    default_dir = tmp_path / "default"
    default_dir.mkdir()
    _write_truth(default_dir / "GTF.json", "默认实体")
    _write_truth(default_dir / "ground_truth.json", "通用实体")
    specified = tmp_path / "active.json"
    _write_truth(specified, "指定实体")

    result = _run_orchestration(ENTITY_REPORT, specified, tmp_path, monkeypatch, default_dir)

    entity = result["entity_eval"]
    assert entity["mode"] == "strict"
    assert entity["ground_truth_path"] == specified.name
    assert [item["name"] for item in entity["expected_entities"]] == ["指定实体"]
    assert entity["metrics"]["overall"]["f1"] == 1.0
    assert result["evaluation_summary"]["entity"]["overall"]["f1"] == 1.0


def test_evaluate_saved_report_passes_selected_sources_to_evidence_scoring(tmp_path):
    _, urls, support = _raw_results()
    sources = [Mock(file_name="spec.pdf")]

    with (
        patch.object(report_evaluation, "evaluate_link_accessibility", AsyncMock(return_value=urls)),
        patch.object(report_evaluation, "evaluate_public_url_sources", Mock(return_value=support)),
        patch.object(report_evaluation, "export_evaluation_report", new=AsyncMock(return_value={
            "markdown": "/outputs/evaluations/eval.md", "word": "", "pdf": "", "errors": []
        })),
    ):
        with_sources = asyncio.run(report_evaluation.evaluate_saved_report(
            task="GTF", run_id="run-sources", report=LOCAL_EVIDENCE_REPORT,
            style_cleanup=None, ground_truth_path=None, selected_sources=sources,
            output_dir=tmp_path,
        ))
        without_sources = asyncio.run(report_evaluation.evaluate_saved_report(
            task="GTF", run_id="run-sources", report=LOCAL_EVIDENCE_REPORT,
            style_cleanup=None, ground_truth_path=None, output_dir=tmp_path,
        ))

    assert with_sources["entity_eval"]["evidence_supported_count"] == 1
    assert without_sources["entity_eval"]["evidence_supported_count"] == 0


def test_reevaluate_saved_run_restores_saved_sources_without_generating_a_report(tmp_path):
    store = EvaluationRecordStore(tmp_path / "records.json")
    store.append_run({
        "run_id": "run-1", "task": "GTF", "evidence_report": LOCAL_EVIDENCE_REPORT,
        "selected_source_files": ["spec.pdf"],
        "report_style_cleanup": {"removed_count": 1},
    })
    _, urls, support = _raw_results()

    with (
        patch.object(report_evaluation, "evaluate_link_accessibility", AsyncMock(return_value=urls)),
        patch.object(report_evaluation, "evaluate_public_url_sources", Mock(return_value=support)),
        patch.object(report_evaluation, "export_evaluation_report", new=AsyncMock(return_value={
            "markdown": "/outputs/evaluations/eval.md", "word": "", "pdf": "", "errors": []
        })),
    ):
        result = asyncio.run(report_evaluation.reevaluate_saved_run(
            store=store, run_id="run-1", ground_truth_path=None, output_dir=tmp_path,
        ))

    assert result["entity_eval"]["evidence_supported_count"] == 1
    assert result["entity_eval"]["extracted_count"] == 1
    assert result["public_url_source_eval"] == support
    history = store.get_run("run-1")["reevaluations"]
    assert len(history) == 1
    assert history[0]["evaluated_at"] == result["evaluated_at"]


def test_export_failure_keeps_metrics_and_reports_a_safe_error(tmp_path):
    _, urls, support = _raw_results()
    with (
        patch.object(report_evaluation, "evaluate_link_accessibility", AsyncMock(return_value=urls)),
        patch.object(report_evaluation, "evaluate_public_url_sources", Mock(return_value=support)),
        patch.object(report_evaluation, "evaluate_report_entities", Mock(return_value=_raw_results()[0])),
        patch.object(report_evaluation, "export_evaluation_report",
                     new=AsyncMock(side_effect=RuntimeError("disk full"))),
    ):
        result = asyncio.run(report_evaluation.evaluate_saved_report(
            task="GTF", run_id="run-export", report=REPORT,
            style_cleanup=None, ground_truth_path=None, output_dir=tmp_path,
        ))

    assert result["evaluation_summary"]["entity"]["mode"] == "strict"
    assert result["evaluation_report_paths"] == {"markdown": "", "word": "", "pdf": ""}
    assert result["evaluation_report_errors"][0]["code"] == "export_failed"
    assert "disk full" not in repr(result)


def test_reevaluation_code_path_never_reaches_generation_services():
    source = Path(report_evaluation.__file__).read_text(encoding="utf-8")

    assert "three_agent_service" not in source
    for agent in ("planner_agent", "research_agent", "writer_agent", "editorial_agent"):
        assert agent not in source


def test_default_output_dir_is_anchored_to_the_project_root():
    assert report_evaluation._default_output_dir().is_absolute()
    assert report_evaluation._default_output_dir().parts[-2:] == ("outputs", "evaluations")


def test_evaluate_saved_report_without_ground_truth_stays_in_proxy_mode(tmp_path, monkeypatch):
    default_dir = tmp_path / "default"
    default_dir.mkdir()
    _write_truth(default_dir / "GTF.json", "默认实体")

    result = _run_orchestration(ENTITY_REPORT, None, tmp_path, monkeypatch, default_dir)

    entity = result["entity_eval"]
    assert entity["mode"] == "proxy"
    assert entity["ground_truth_status"] == "missing"
    assert entity["metrics"] is None
    assert result["evaluation_summary"]["entity"]["mode"] == "proxy"
