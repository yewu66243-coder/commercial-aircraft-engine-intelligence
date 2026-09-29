from __future__ import annotations

import asyncio
import ssl
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError, URLError

import pytest

from three_agent_service import ThreeAgentRequestData, ThreeAgentService


def _orchestrated_evaluation(*, summary_status="completed"):
    return {
        "evaluated_at": "2026-09-29T12:00:00+08:00",
        "url_check": {
            "total_urls": 1, "checked_urls": 1, "accessible_urls": 1,
            "failed_urls": 0, "skipped_urls": 0, "accessibility_rate": 1.0,
            "results": [{"url": "https://example.test", "accessible": True}],
        },
        "public_url_source_eval": {
            "relationship_count": 1, "supported_count": 1,
            "partially_supported_count": 0, "unsupported_count": 0,
            "unchecked_count": 0, "support_accuracy": 1.0,
            "requirement_met": True, "relationships": [],
        },
        "entity_eval": {"status": "auto_evaluated", "mode": "strict",
                        "metrics": {}, "requirement_met": True,
                        "auto_evidence_eval": {}},
        "evaluation_summary": {
            "status": summary_status,
            "entity": {"status": "completed", "requirement_met": True},
            "public_links": {
                "accessibility": {"status": "completed", "requirement_met": True},
                "claim_support": {"status": "completed", "requirement_met": True},
            },
            "style_cleanup": {"removed_count": 1},
            "evaluation_report_paths": {
                "markdown": "/outputs/evaluations/eval.md",
                "word": "/outputs/evaluations/eval.docx",
                "pdf": "/outputs/evaluations/eval.pdf",
            },
            "errors": [],
        },
        "evaluation_report_paths": {
            "markdown": "/outputs/evaluations/eval.md",
            "word": "/outputs/evaluations/eval.docx",
            "pdf": "/outputs/evaluations/eval.pdf",
        },
        "evaluation_report_errors": [],
    }


def _failed_orchestrated_evaluation(url_count=0):
    result = _orchestrated_evaluation(summary_status="partial")
    result["url_check"] = {
        "total_urls": url_count, "checked_urls": 0, "accessible_urls": 0,
        "failed_urls": 0, "skipped_urls": url_count, "accessibility_rate": None,
        "results": [], "evaluation_error": "RuntimeError",
    }
    result["public_url_source_eval"] = {
        "relationship_count": 0, "supported_count": 0,
        "partially_supported_count": 0, "unsupported_count": 0,
        "unchecked_count": 0, "support_accuracy": None, "relationships": [],
    }
    result["entity_eval"] = {
        "status": "evaluation_failed", "mode": "proxy", "metrics": None,
        "evaluation_error": "ValueError", "auto_evidence_eval": {},
    }
    result["evaluation_summary"] = {
        "status": "partial",
        "entity": {"status": "evaluation_failed", "matched": []},
        "public_links": {
            "accessibility": {"status": "evaluation_failed", "requirement_met": None},
            "claim_support": {"status": "no_public_relationships", "requirement_met": None},
            "status": "evaluation_failed", "accessibility_rate": None,
        },
        "style_cleanup": {"removed_count": 0},
        "evaluation_report_paths": result["evaluation_report_paths"],
        "errors": [],
    }
    return result


def test_run_statistics_uses_relationship_denominator_for_public_support() -> None:
    service = ThreeAgentService(ThreeAgentRequestData(task="关系口径"))
    stats = {
        "run_id": "test-run",
        "task": "关系口径",
        "report_source": "web",
        "query_domain_count": 0,
        "selected_source_count": 0,
        "duration_seconds": 1.0,
        "duration_minutes": 0.02,
        "url_check": {
            "total_urls": 1,
            "checked_urls": 1,
            "accessible_urls": 1,
            "failed_urls": 0,
            "accessibility_rate": 1.0,
        },
        "public_url_source_eval": {
            "relationship_count": 3,
            "cited_url_ref_count": 1,
            "unique_url_count": 1,
            "supported_count": 1,
            "partially_supported_count": 1,
            "unsupported_count": 0,
            "unchecked_count": 1,
            "support_accuracy": 1 / 3,
            "threshold": 0.98,
            "requirement_met": False,
        },
    }

    section = service.build_run_statistics_section(stats)

    assert "| 断言—公开链接关系数量 | 3 |" in section
    assert "| 公开链接支撑关系数量 | 1 |" in section
    assert "| 公开链接部分支撑关系数量 | 1 |" in section
    assert "| 公开链接未核验关系数量 | 1 |" in section
    assert "正文引用 URL 编号数量" not in section
    assert "分母为正文中的断言—公开链接关系总数" in section
    assert "分子仅计完全支撑的关系" in section


def test_run_statistics_translates_link_checker_failure_labels() -> None:
    service = ThreeAgentService(ThreeAgentRequestData(task="异常标签"))
    stats = {
        "run_id": "test-run",
        "task": "异常标签",
        "report_source": "web",
        "query_domain_count": 0,
        "selected_source_count": 0,
        "duration_seconds": 1.0,
        "duration_minutes": 0.02,
        "url_check": {
            "total_urls": 2,
            "checked_urls": 2,
            "accessible_urls": 0,
            "failed_urls": 2,
            "accessibility_rate": 0.0,
            "failure_reasons": {"connection": 1, "checker_exception": 1},
        },
    }

    section = service.build_run_statistics_section(stats)

    assert "连接异常：1" in section
    assert "检查器异常：1" in section
    assert "checker_exception" not in section


class _Response:
    def __init__(self, status: int):
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _request_method(call) -> str:
    return call.args[0].get_method()


def test_service_clean_url_candidate_remains_compatible() -> None:
    assert ThreeAgentService._clean_url_candidate("https://example.test，") == "https://example.test"


def test_url_check_accepts_only_final_2xx_or_3xx():
    with patch("gpt_researcher.evaluation.link_accessibility.urlopen", return_value=_Response(302)) as opener:
        result = ThreeAgentService._check_url_sync("https://example.com")

    assert result["accessible"] is True
    assert result["status_code"] == 302
    assert _request_method(opener.call_args) == "HEAD"


def test_head_403_retries_get_and_uses_get_result():
    denied = HTTPError("https://example.com", 403, "Forbidden", None, None)
    with patch("gpt_researcher.evaluation.link_accessibility.urlopen", side_effect=[denied, _Response(204)]) as opener:
        result = ThreeAgentService._check_url_sync("https://example.com")

    assert result["accessible"] is True
    assert result["method"] == "GET"
    assert [_request_method(call) for call in opener.call_args_list] == ["HEAD", "GET"]


def test_head_405_retry_with_final_404_is_inaccessible():
    not_allowed = HTTPError("https://example.com", 405, "Method Not Allowed", None, None)
    not_found = HTTPError("https://example.com", 404, "Not Found", None, None)
    with patch("gpt_researcher.evaluation.link_accessibility.urlopen", side_effect=[not_allowed, not_found]):
        result = ThreeAgentService._check_url_sync("https://example.com")

    assert result["accessible"] is False
    assert result["status_code"] == 404
    assert result["failure_reason"] == "http_status"


def test_tls_failure_is_inaccessible_without_unverified_retry():
    error = URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    with patch("gpt_researcher.evaluation.link_accessibility.urlopen", side_effect=error) as opener:
        result = ThreeAgentService._check_url_sync("https://example.com")

    assert result["accessible"] is False
    assert result["failure_reason"] == "ssl_certificate"
    assert result["ssl_verified"] is True
    assert opener.call_count == 1


def test_inspection_checks_all_unique_report_urls_by_default():
    service = ThreeAgentService(ThreeAgentRequestData(task="链接测评"))
    report = "\n".join(f"https://example.com/source/{index}" for index in range(55))
    with patch.object(
        service,
        "_check_url_sync",
        side_effect=lambda url: {
            "url": url,
            "accessible": True,
            "failure_reason": "",
            "ssl_verified": True,
        },
    ) as checker:
        result = asyncio.run(service.inspect_report_urls(report))

    assert checker.call_count == 55
    assert result["total_urls"] == 55
    assert result["checked_urls"] == 55
    assert result["skipped_urls"] == 0
    assert result["accessibility_rate"] == 1.0


@pytest.mark.parametrize(
    ("source_url", "expected_url_count"),
    [
        ("https://example.com/source", 1),
        ("https://example.com：80/source", 0),
    ],
)
def test_pipeline_publishes_failed_evaluations_without_blocking_exports(
    source_url, expected_url_count
):
    service = ThreeAgentService(ThreeAgentRequestData(task="容错测评"))
    service.generation_status = "ready"
    report = f"# 容错测评报告\n\n正文：{source_url}"
    prepared = SimpleNamespace(
        markdown=report,
        quality={"status": "ready", "warnings": []},
        citation_map={},
        verification_notes=[],
    )
    with ExitStack() as stack:
        stack.enter_context(patch.object(service, "pre_search_abstracts", new=AsyncMock()))
        stack.enter_context(patch.object(service, "planner_agent", return_value=[]))
        stack.enter_context(patch.object(service, "research_agent", new=AsyncMock(return_value=[])))
        stack.enter_context(patch.object(service, "collect_report_images"))
        stack.enter_context(patch.object(service, "writer_agent", new=AsyncMock(return_value=report)))
        stack.enter_context(patch.object(service, "editorial_agent", new=AsyncMock(return_value=report)))
        stack.enter_context(patch.object(service, "append_evaluation_record", return_value="record.json"))
        stack.enter_context(patch("three_agent_service.build_source_catalog", return_value={"sources": [], "errors": []}))
        stack.enter_context(patch("three_agent_service.evaluate_saved_report", new=AsyncMock(
            return_value=_failed_orchestrated_evaluation(expected_url_count))))
        stack.enter_context(patch("three_agent_service.prepare_formal_report", return_value=prepared))
        stack.enter_context(
            patch(
                "three_agent_service.review_content",
                return_value={"warnings": []},
            )
        )
        md_export = stack.enter_context(
            patch("three_agent_service.write_text_to_md", new=AsyncMock(return_value="outputs/report.md"))
        )
        pdf_export = stack.enter_context(
            patch("three_agent_service.write_md_to_pdf", new=AsyncMock(return_value="outputs/report.pdf"))
        )
        word_export = stack.enter_context(
            patch("three_agent_service.write_md_to_word", new=AsyncMock(return_value="outputs/report.docx"))
        )

        result = asyncio.run(service.run())

    assert all(result["export_status"].values())
    assert md_export.await_count == pdf_export.await_count == word_export.await_count == 1
    summary = result["run_statistics"]["evaluation_summary"]
    assert summary["status"] == "partial"
    assert summary["entity"]["status"] == "evaluation_failed"
    assert summary["public_links"]["status"] == "evaluation_failed"
    assert summary["public_links"]["claim_support"]["status"] == "no_public_relationships"
    assert summary["public_links"]["accessibility_rate"] is None
    assert "private" not in repr(summary)
    assert result["run_statistics"]["url_check"]["total_urls"] == expected_url_count


def test_summary_assembly_failure_does_not_block_exports():
    service = ThreeAgentService(ThreeAgentRequestData(task="汇总容错"))
    service.generation_status = "ready"
    report = "# 汇总容错报告\n\n正文。"
    prepared = SimpleNamespace(
        markdown=report,
        quality={"status": "ready", "warnings": []},
        citation_map={},
        verification_notes=[],
    )
    with ExitStack() as stack:
        stack.enter_context(patch.object(service, "pre_search_abstracts", new=AsyncMock()))
        stack.enter_context(patch.object(service, "planner_agent", return_value=[]))
        stack.enter_context(patch.object(service, "research_agent", new=AsyncMock(return_value=[])))
        stack.enter_context(patch.object(service, "collect_report_images"))
        stack.enter_context(patch.object(service, "writer_agent", new=AsyncMock(return_value=report)))
        stack.enter_context(patch.object(service, "editorial_agent", new=AsyncMock(return_value=report)))
        stack.enter_context(patch.object(service, "append_evaluation_record", return_value="record.json"))
        stack.enter_context(patch("three_agent_service.build_source_catalog", return_value={"sources": [], "errors": []}))
        stack.enter_context(patch("three_agent_service.evaluate_saved_report",
                                  new=AsyncMock(side_effect=RuntimeError("private summary"))))
        stack.enter_context(patch("three_agent_service.build_evaluation_summary",
                                  side_effect=RuntimeError("private fallback")))
        stack.enter_context(patch("three_agent_service.prepare_formal_report", return_value=prepared))
        stack.enter_context(patch("three_agent_service.review_content", return_value={"warnings": []}))
        exports = [
            stack.enter_context(
                patch(name, new=AsyncMock(return_value=path))
            )
            for name, path in (
                ("three_agent_service.write_text_to_md", "outputs/report.md"),
                ("three_agent_service.write_md_to_pdf", "outputs/report.pdf"),
                ("three_agent_service.write_md_to_word", "outputs/report.docx"),
            )
        ]

        result = asyncio.run(service.run())

    assert all(export.await_count == 1 for export in exports)
    summary = result["run_statistics"]["evaluation_summary"]
    assert summary["status"] == "failed"
    assert summary["entity"]["matched"] == []
    assert summary["public_links"]["accessibility"]["status"] == "evaluation_failed"
    assert summary["public_links"]["accessibility_rate"] is None
    assert summary["style_cleanup"] == {"removed_count": 0}
    assert summary["evaluation_report_paths"] == {"markdown": "", "word": "", "pdf": ""}
    assert "private summary" not in repr(summary)


def test_pipeline_evaluates_and_exports_the_cleaned_post_editorial_report():
    service = ThreeAgentService(ThreeAgentRequestData(task="清理后测评"))
    service.generation_status = "ready"
    draft = "# 清理后测评\n\n事实成立。本报告不作确定性结论。"
    edited = draft + "\n\n校订完成。"
    cleaned = "# 清理后测评\n\n事实成立。\n\n校订完成。"
    prepared = SimpleNamespace(markdown=cleaned, quality={"status": "ready", "warnings": []},
                               citation_map={}, verification_notes=[])
    saved = []
    evaluation = _orchestrated_evaluation()

    with ExitStack() as stack:
        stack.enter_context(patch.object(service, "pre_search_abstracts", new=AsyncMock()))
        stack.enter_context(patch.object(service, "planner_agent", return_value=[]))
        stack.enter_context(patch.object(service, "research_agent", new=AsyncMock(return_value=[])))
        stack.enter_context(patch.object(service, "collect_report_images"))
        stack.enter_context(patch.object(service, "writer_agent", new=AsyncMock(return_value=draft)))
        stack.enter_context(patch.object(service, "editorial_agent", new=AsyncMock(return_value=edited)))
        cleaner = stack.enter_context(patch("three_agent_service.clean_formal_report_style",
                                            return_value=(cleaned, {"removed_count": 1})))
        evaluator = stack.enter_context(patch("three_agent_service.evaluate_saved_report",
                                              new=AsyncMock(return_value=evaluation)))
        stack.enter_context(patch.object(service, "append_evaluation_record",
                                         side_effect=lambda record: saved.append(record) or "records.json"))
        stack.enter_context(patch("three_agent_service.build_source_catalog",
                                  return_value={"sources": [], "errors": []}))
        formatter = stack.enter_context(patch("three_agent_service.prepare_formal_report",
                                              return_value=prepared))
        stack.enter_context(patch("three_agent_service.review_content", return_value={"warnings": []}))
        exporters = [stack.enter_context(patch(name, new=AsyncMock(return_value=path)))
                     for name, path in (
                         ("three_agent_service.write_text_to_md", "outputs/report.md"),
                         ("three_agent_service.write_md_to_pdf", "outputs/report.pdf"),
                         ("three_agent_service.write_md_to_word", "outputs/report.docx"),
                     )]

        result = asyncio.run(service.run())

    cleaner.assert_called_once_with(edited)
    assert evaluator.await_args.kwargs["report"] == cleaned
    formatter.assert_called_once()
    assert formatter.call_args.args[0] == cleaned
    assert all(exporter.await_args.args[0] == cleaned for exporter in exporters)
    assert saved[0]["record_version"] == "3.0.0"
    assert saved[0]["evidence_report"] == cleaned
    assert saved[0]["report_style_cleanup"] == {"removed_count": 1}
    assert saved[0]["evaluation_report_paths"]["word"].endswith("eval.docx")
    assert result["run_statistics"]["validation_summary"]["url_requirement_met"] is True


def test_catastrophic_evaluation_orchestration_failure_does_not_block_original_exports():
    service = ThreeAgentService(ThreeAgentRequestData(task="测评编排失败"))
    service.generation_status = "ready"
    report = "# 测评编排失败\n\n事实。"
    prepared = SimpleNamespace(markdown=report, quality={"status": "ready", "warnings": []},
                               citation_map={}, verification_notes=[])

    with ExitStack() as stack:
        stack.enter_context(patch.object(service, "pre_search_abstracts", new=AsyncMock()))
        stack.enter_context(patch.object(service, "planner_agent", return_value=[]))
        stack.enter_context(patch.object(service, "research_agent", new=AsyncMock(return_value=[])))
        stack.enter_context(patch.object(service, "collect_report_images"))
        stack.enter_context(patch.object(service, "writer_agent", new=AsyncMock(return_value=report)))
        stack.enter_context(patch.object(service, "editorial_agent", new=AsyncMock(return_value=report)))
        stack.enter_context(patch("three_agent_service.clean_formal_report_style",
                                  return_value=(report, {"removed_count": 0})))
        stack.enter_context(patch("three_agent_service.evaluate_saved_report",
                                  new=AsyncMock(side_effect=RuntimeError("private"))))
        stack.enter_context(patch.object(service, "append_evaluation_record", return_value="records.json"))
        stack.enter_context(patch("three_agent_service.build_source_catalog",
                                  return_value={"sources": [], "errors": []}))
        stack.enter_context(patch("three_agent_service.prepare_formal_report", return_value=prepared))
        stack.enter_context(patch("three_agent_service.review_content", return_value={"warnings": []}))
        exports = [stack.enter_context(patch(name, new=AsyncMock(return_value=path)))
                   for name, path in (
                       ("three_agent_service.write_text_to_md", "outputs/report.md"),
                       ("three_agent_service.write_md_to_pdf", "outputs/report.pdf"),
                       ("three_agent_service.write_md_to_word", "outputs/report.docx"),
                   )]

        result = asyncio.run(service.run())

    assert all(exporter.await_count == 1 for exporter in exports)
    assert result["run_statistics"]["evaluation_summary"]["status"] == "failed"
    assert result["run_statistics"]["evaluation_report_paths"] == {
        "markdown": "", "word": "", "pdf": ""
    }
