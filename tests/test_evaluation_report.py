from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.reporting import evaluation_report
from gpt_researcher.evaluation.evaluation_summary import build_evaluation_summary


def _results():
    entity = {
        "mode": "strict", "ground_truth_path": r"C:\private\truth.json",
        "metrics": {
            "overall": {"true_positive": 2, "false_positive": 1, "false_negative": 1,
                        "precision": .5, "recall": .5, "f1": .5},
            "categories": {"organization": {"label": "机构", "true_positive": 2,
                           "false_positive": 1, "false_negative": 1, "precision": .5,
                           "recall": .5, "f1": .5}},
            "matches": [{"entity": "GE"}], "wrong_entities": [{"entity": "CFM"}],
            "missed_entities": [{"entity": "RR"}],
        },
    }
    urls = {"total_urls": 2, "checked_urls": 2, "accessible_urls": 1, "failed_urls": 1,
            "results": [{"url": "https://example.test/one", "accessible": True,
                         "status_code": 200, "method": "HEAD"},
                        {"url": "https://example.test/two", "accessible": False,
                         "failure_reason": "timeout"}]}
    support = {"relationship_count": 2, "supported_count": 1,
               "partially_supported_count": 0, "unsupported_count": 1, "unchecked_count": 0,
               "relationships": [{"claim": "GE 生产发动机", "url": "https://example.test/one",
                                  "status": "supported", "confidence": .95,
                                  "reason": "SECRET RAW REASON"},
                                 {"claim": "RR 声称", "url": "https://example.test/two",
                                  "status": "unsupported", "confidence": .2}]}
    return entity, urls, support


def _report(summary=None, *, entity=None, urls=None, support=None):
    default_entity, default_urls, default_support = _results()
    entity = default_entity if entity is None else entity
    urls = default_urls if urls is None else urls
    support = default_support if support is None else support
    summary = summary if summary is not None else build_evaluation_summary(
        entity, urls, support, {"removed_count": 3, "removed_items": ["SECRET ORIGINAL REPORT"]})
    return evaluation_report.render_evaluation_report_markdown(
        task="发动机市场", run_id="run-123", evaluated_at="2026-09-29T01:02:03Z",
        summary=summary, entity_eval=entity, url_check=urls, url_source_eval=support)


def test_strict_report_contains_metrics_details_definitions_and_safe_metadata():
    report = _report()
    for expected in ("# 独立测评报告", "任务名称", "发动机市场", "测评时间",
                     "报告标识", "run-123", "truth.json", "精确率", "召回率", "F1",
                     "90%", "98%", "实体总体", "TP", "FP", "FN", "机构",
                     "GE", "CFM", "RR", "唯一URL可访问率", "https://example.test/one",
                     "timeout", "断言—URL支撑准确率", "GE 生产发动机", "95%",
                     "来源充分支撑该断言。", "规范清理数量", "3"):
        assert expected in report
    assert "style removed_count" in report
    assert "SECRET ORIGINAL REPORT" not in report
    assert "SECRET RAW REASON" not in report
    assert r"C:\private" not in report


def test_proxy_has_explicit_limitation_and_missing_metrics_are_dashes():
    entity = {"mode": "proxy", "auto_evidence_eval": {"auto_evidence_accuracy": .75}}
    summary = build_evaluation_summary(entity, {"total_urls": 0}, None)
    report = _report(summary, entity=entity)
    assert "证据支撑率仅为代理指标，不能替代实体准确率" in report
    assert "75%" in report
    assert "—" in report
    assert "无公开URL" in report
    assert "未测评" in report


def test_partial_failed_and_invalid_states_have_clear_copy_without_raw_errors():
    entity = {"mode": "invalid", "evaluation_error": "API_KEY=secret"}
    urls = {"evaluation_error": "private URL crash"}
    support = {"evaluation_error": "private support crash"}
    report = _report(build_evaluation_summary(entity, urls, support), entity=entity,
                     urls=urls, support=support)
    assert "部分完成" in report or "测评失败" in report
    assert "标准答案无效" in report
    assert "测评未完成" in report
    assert "降级说明" in report
    for secret in ("API_KEY", "secret", "private URL crash", "private support crash"):
        assert secret not in report


def test_summary_whitelist_and_raw_secrets_never_leak():
    summary = build_evaluation_summary(*_results())
    summary["source_path"] = r"C:\private\source.md"
    summary["debug"] = "DEBUG_SECRET"
    summary["entity"]["matched"][0]["headers"] = {"Authorization": "Bearer TOKEN"}
    summary["style_cleanup"]["removed_items"] = ["ORIGINAL BODY"]
    raw = {"source_path": r"C:\private\raw.md", "error": "RAW_ERROR",
           "headers": {"Authorization": "API_KEY_X"}, "report_body": "ORIGINAL REPORT"}
    report = _report(summary, entity=raw, urls=raw, support=raw)
    for secret in ("C:\\private", "DEBUG_SECRET", "Bearer TOKEN", "ORIGINAL BODY",
                   "RAW_ERROR", "API_KEY_X", "ORIGINAL REPORT"):
        assert secret not in report


def test_table_cells_escape_pipes_backslashes_newlines_and_truncate():
    entity, urls, support = _results()
    long_claim = "A" * 1200 + "TAIL_SECRET"
    support["relationships"][0]["claim"] = "a|b\\c\nd" + long_claim
    urls["results"][0]["url"] = "https://example.test/" + "x" * 1200 + "URL_TAIL_SECRET"
    summary = build_evaluation_summary(entity, urls, support)
    report = _report(summary, entity=entity, urls=urls, support=support)
    assert "a\\|b\\\\c d" in report
    assert "TAIL_SECRET" not in report
    assert "URL_TAIL_SECRET" not in report


def test_rendering_is_deterministic():
    assert _report() == _report()


def test_url_credentials_and_local_paths_are_not_published():
    entity, urls, support = _results()
    urls["results"][0]["url"] = "https://user:pass@example.test/one?api_key=PRIVATE_KEY#secret"
    support["relationships"][0]["url"] = urls["results"][0]["url"]
    support["relationships"][0]["claim"] = r"evidence C:\private\report.txt"
    report = _report(build_evaluation_summary(entity, urls, support), entity=entity,
                     urls=urls, support=support)
    assert "https://example.test/one" in report
    for secret in ("user:pass", "PRIVATE_KEY", "#secret", r"C:\private\report.txt"):
        assert secret not in report


def test_export_writes_markdown_and_passes_same_text_to_both_renderers(tmp_path, monkeypatch):
    calls = []

    def fake_word(text, destination, base_path=None):
        calls.append(("word", text, Path(destination), base_path))
        Path(destination).write_text("word", encoding="utf-8")

    def fake_pdf(text, destination, base_path=None):
        calls.append(("pdf", text, Path(destination), base_path))
        Path(destination).write_text("pdf", encoding="utf-8")

    monkeypatch.setattr(evaluation_report, "render_word", fake_word)
    monkeypatch.setattr(evaluation_report, "render_pdf", fake_pdf)
    result = asyncio.run(evaluation_report.export_evaluation_report(
        markdown="中文测评", task="发动机", run_id="run-1234567890123456",
        evaluated_at="2026-09-29T01:02:03.123456Z", output_dir=tmp_path))
    assert result["errors"] == []
    assert all(result[k].startswith("/outputs/evaluations/") for k in ("markdown", "word", "pdf"))
    assert len(calls) == 2
    assert all(c[1] == "中文测评" for c in calls)
    assert (tmp_path / Path(result["markdown"]).name).read_text(encoding="utf-8") == "中文测评"
    assert all((tmp_path / Path(result[k]).name).exists() for k in ("word", "pdf"))


@pytest.mark.parametrize("failed", ["word", "pdf", "both"])
def test_format_failures_are_isolated_and_safe(tmp_path, monkeypatch, failed):
    def renderer(kind):
        def render(_text, destination, base_path=None):
            if failed in {kind, "both"}:
                raise RuntimeError("PRIVATE API KEY")
            Path(destination).write_text(kind, encoding="utf-8")
        return render

    monkeypatch.setattr(evaluation_report, "render_word", renderer("word"))
    monkeypatch.setattr(evaluation_report, "render_pdf", renderer("pdf"))
    result = asyncio.run(evaluation_report.export_evaluation_report(
        markdown="report", task="task", run_id="run", evaluated_at="2026-09-29T00:00:00Z",
        output_dir=tmp_path))
    assert result["markdown"]
    assert bool(result["word"]) == (failed == "pdf")
    assert bool(result["pdf"]) == (failed == "word")
    assert len(result["errors"]) == (2 if failed == "both" else 1)
    assert "PRIVATE" not in repr(result)


def test_markdown_failure_skips_both_renderers_and_hides_exception(tmp_path, monkeypatch):
    def fail_write(*_args, **_kwargs):
        raise OSError("PRIVATE path")

    monkeypatch.setattr(evaluation_report, "_write_markdown_atomic", fail_write)
    monkeypatch.setattr(evaluation_report, "render_word", lambda *_a, **_k: pytest.fail("word called"))
    monkeypatch.setattr(evaluation_report, "render_pdf", lambda *_a, **_k: pytest.fail("pdf called"))
    result = asyncio.run(evaluation_report.export_evaluation_report(
        markdown="report", task="task", run_id="run", evaluated_at="2026-09-29T00:00:00Z",
        output_dir=tmp_path))
    assert result["markdown"] == result["word"] == result["pdf"] == ""
    assert result["errors"] == [{"format": "markdown", "code": "export_failed",
                                  "message": "Markdown测评报告导出失败。"}]
    assert "PRIVATE" not in repr(result)


@pytest.mark.parametrize("task", ["CON", "aux. ", 'bad<>:"/\\|?*name'])
def test_windows_filename_is_safe(tmp_path, monkeypatch, task):
    monkeypatch.setattr(evaluation_report, "render_word", lambda *_a, **_k: None)
    monkeypatch.setattr(evaluation_report, "render_pdf", lambda *_a, **_k: None)
    result = asyncio.run(evaluation_report.export_evaluation_report(
        markdown="report", task=task, run_id="../RUN:*?", evaluated_at="2026-09-29T00:00:00Z",
        output_dir=tmp_path))
    name = Path(result["markdown"]).name
    assert name
    assert not any(char in name for char in '<>:"/\\|?*')
    assert not name.split("_")[0].upper() in {"CON", "AUX", "NUL", "PRN"}
    assert (tmp_path / name).exists()


def test_second_export_preserves_first_report(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation_report, "render_word", lambda *_a, **_k: None)
    monkeypatch.setattr(evaluation_report, "render_pdf", lambda *_a, **_k: None)
    args = {"task": "task", "run_id": "run", "evaluated_at": "2026-09-29T00:00:00Z",
            "output_dir": tmp_path}
    first = asyncio.run(evaluation_report.export_evaluation_report(markdown="first", **args))
    second = asyncio.run(evaluation_report.export_evaluation_report(markdown="second", **args))
    assert first["markdown"] != second["markdown"]
    assert (tmp_path / Path(first["markdown"]).name).read_text(encoding="utf-8") == "first"
    assert (tmp_path / Path(second["markdown"]).name).read_text(encoding="utf-8") == "second"


def test_invalid_non_string_timestamp_falls_back_to_safe_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation_report, "render_word", lambda *_a, **_k: None)
    monkeypatch.setattr(evaluation_report, "render_pdf", lambda *_a, **_k: None)

    result = asyncio.run(evaluation_report.export_evaluation_report(
        markdown="report", task="task", run_id="run", evaluated_at=None, output_dir=tmp_path))

    assert result["errors"] == []
    assert "undated_" in result["markdown"]
    assert (tmp_path / Path(result["markdown"]).name).exists()
