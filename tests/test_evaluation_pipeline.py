from __future__ import annotations

import asyncio
import ssl
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError, URLError

from three_agent_service import ThreeAgentRequestData, ThreeAgentService


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


def test_url_check_accepts_only_final_2xx_or_3xx():
    with patch("three_agent_service.urlopen", return_value=_Response(302)) as opener:
        result = ThreeAgentService._check_url_sync("https://example.com")

    assert result["accessible"] is True
    assert result["status_code"] == 302
    assert _request_method(opener.call_args) == "HEAD"


def test_head_403_retries_get_and_uses_get_result():
    denied = HTTPError("https://example.com", 403, "Forbidden", None, None)
    with patch("three_agent_service.urlopen", side_effect=[denied, _Response(204)]) as opener:
        result = ThreeAgentService._check_url_sync("https://example.com")

    assert result["accessible"] is True
    assert result["method"] == "GET"
    assert [_request_method(call) for call in opener.call_args_list] == ["HEAD", "GET"]


def test_head_405_retry_with_final_404_is_inaccessible():
    not_allowed = HTTPError("https://example.com", 405, "Method Not Allowed", None, None)
    not_found = HTTPError("https://example.com", 404, "Not Found", None, None)
    with patch("three_agent_service.urlopen", side_effect=[not_allowed, not_found]):
        result = ThreeAgentService._check_url_sync("https://example.com")

    assert result["accessible"] is False
    assert result["status_code"] == 404
    assert result["failure_reason"] == "http_status"


def test_tls_failure_is_inaccessible_without_unverified_retry():
    error = URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    with patch("three_agent_service.urlopen", side_effect=error) as opener:
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


def test_pipeline_publishes_failed_evaluations_without_blocking_exports():
    service = ThreeAgentService(ThreeAgentRequestData(task="容错测评"))
    service.generation_status = "ready"
    report = "# 容错测评报告\n\n正文：https://example.com/source"
    prepared = SimpleNamespace(
        markdown=report,
        quality={"status": "ready", "warnings": []},
        citation_map={},
        verification_notes=[],
    )
    url_error = RuntimeError("private network detail")
    entity_error = ValueError("private entity detail")

    with ExitStack() as stack:
        stack.enter_context(patch.object(service, "pre_search_abstracts", new=AsyncMock()))
        stack.enter_context(patch.object(service, "planner_agent", return_value=[]))
        stack.enter_context(patch.object(service, "research_agent", new=AsyncMock(return_value=[])))
        stack.enter_context(patch.object(service, "collect_report_images"))
        stack.enter_context(patch.object(service, "writer_agent", new=AsyncMock(return_value=report)))
        stack.enter_context(patch.object(service, "editorial_agent", new=AsyncMock(return_value=report)))
        stack.enter_context(patch.object(service, "inspect_report_urls", new=AsyncMock(side_effect=url_error)))
        stack.enter_context(patch.object(service, "append_evaluation_record", return_value="record.json"))
        stack.enter_context(patch("three_agent_service.build_source_catalog", return_value={"sources": [], "errors": []}))
        stack.enter_context(patch("three_agent_service.evaluate_public_url_sources", return_value={}))
        stack.enter_context(
            patch(
                "three_agent_service.prune_redundant_unchecked_url_citations",
                side_effect=lambda text, _stats: (text, {"changed": False}),
            )
        )
        stack.enter_context(patch("three_agent_service.evaluate_report_entities", side_effect=entity_error))
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
    assert summary["status"] == "failed"
    assert summary["entity"]["status"] == "evaluation_failed"
    assert summary["public_links"]["status"] == "evaluation_failed"
    assert "private" not in repr(summary)
    assert result["run_statistics"]["url_check"]["total_urls"] == 1


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
    url_check = {
        "total_urls": 0,
        "checked_urls": 0,
        "accessible_urls": 0,
        "failed_urls": 0,
        "accessibility_rate": None,
        "results": [],
    }
    entity_eval = {
        "status": "auto_evidence_checked",
        "mode": "proxy",
        "metrics": None,
        "auto_evidence_eval": {"auto_evidence_accuracy": 0.8},
    }

    with ExitStack() as stack:
        stack.enter_context(patch.object(service, "pre_search_abstracts", new=AsyncMock()))
        stack.enter_context(patch.object(service, "planner_agent", return_value=[]))
        stack.enter_context(patch.object(service, "research_agent", new=AsyncMock(return_value=[])))
        stack.enter_context(patch.object(service, "collect_report_images"))
        stack.enter_context(patch.object(service, "writer_agent", new=AsyncMock(return_value=report)))
        stack.enter_context(patch.object(service, "editorial_agent", new=AsyncMock(return_value=report)))
        stack.enter_context(patch.object(service, "inspect_report_urls", new=AsyncMock(return_value=url_check)))
        stack.enter_context(patch.object(service, "append_evaluation_record", return_value="record.json"))
        stack.enter_context(patch("three_agent_service.build_source_catalog", return_value={"sources": [], "errors": []}))
        stack.enter_context(patch("three_agent_service.evaluate_public_url_sources", return_value={}))
        stack.enter_context(
            patch(
                "three_agent_service.prune_redundant_unchecked_url_citations",
                side_effect=lambda text, _stats: (text, {"changed": False}),
            )
        )
        stack.enter_context(patch("three_agent_service.evaluate_report_entities", return_value=entity_eval))
        stack.enter_context(patch("three_agent_service.build_evaluation_summary", side_effect=RuntimeError("private summary")))
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
    assert result["run_statistics"]["evaluation_summary"]["status"] == "failed"
    assert "private summary" not in repr(result["run_statistics"]["evaluation_summary"])
