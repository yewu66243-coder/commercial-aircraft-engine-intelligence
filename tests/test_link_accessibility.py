from __future__ import annotations

import asyncio
import io
import socket
import ssl
import time
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from gpt_researcher.evaluation.link_accessibility import (
    check_url_sync,
    evaluate_link_accessibility,
    extract_public_urls,
    normalize_url_for_request,
)


class _Response:
    def __init__(self, status: int):
        self.status = status

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _request_method(call) -> str:
    return call.args[0].get_method()


def test_extract_public_urls_cleans_trailing_markdown_and_chinese_punctuation() -> None:
    report = (
        "[source](https://example.com/a). https://example.com/a，"
        "另见 https://example.org/b） file:///tmp/not-public"
    )

    assert extract_public_urls(report) == ["https://example.com/a", "https://example.org/b"]


def test_extract_public_urls_preserves_balanced_delimiters_and_uppercase_scheme() -> None:
    report = (
        "[nested](HTTPS://example.com/a_(b)) "
        "https://[2001:db8::1]/x "
        "https://example.com/search?arr[0]=engine "
        "https://example.com/end， "
        "https://example.com/end，"
    )

    assert extract_public_urls(report) == [
        "HTTPS://example.com/a_(b)",
        "https://[2001:db8::1]/x",
        "https://example.com/search?arr[0]=engine",
        "https://example.com/end",
    ]


def test_extract_public_urls_stops_at_gfm_table_pipes_including_escaped_pipes() -> None:
    report = "| source | https://example.com/a | note |\n| source | https://example.org/b\\|note |"

    assert extract_public_urls(report) == ["https://example.com/a", "https://example.org/b"]


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("https://user:pass@example.com/a", "pass"),
        ("https://user@example.com/a", "user"),
        ("https://用户:密码@example.com/a", "密码"),
    ],
)
def test_normalize_url_for_request_rejects_userinfo_without_leaking_it(
    url: str, secret: str
) -> None:
    with pytest.raises(ValueError, match="userinfo"):
        normalize_url_for_request(url)

    result = check_url_sync(url)

    assert result["accessible"] is False
    assert result["method"] == "normalize"
    assert result["failure_reason"] == "invalid_url"
    assert secret not in str(result["url"])
    assert secret not in str(result["checked_url"])
    assert secret not in str(result["error"])


def test_normalize_url_for_request_preserves_ip_addresses_and_normalizes_invalid_percent() -> None:
    assert normalize_url_for_request(
        "HTTPS://例子.测试/a%2Fb?next=%2F&arr%5B0%5D=1"
    ) == "https://xn--fsqu00a.xn--0zwm56d/a%2Fb?next=%2F&arr%5B0%5D=1"
    assert normalize_url_for_request(
        "https://[2001:db8::1]:8443/a%2Fb?value=%25"
    ) == "https://[2001:db8::1]:8443/a%2Fb?value=%25"
    assert normalize_url_for_request(
        "https://example.com/a%2G?value=%&other=%zz"
    ) == "https://example.com/a%252G?value=%25&other=%25zz"


@pytest.mark.parametrize("status", [200, 302])
def test_check_url_sync_accepts_2xx_and_3xx(status: int) -> None:
    with patch(
        "gpt_researcher.evaluation.link_accessibility.urlopen", return_value=_Response(status)
    ) as opener:
        result = check_url_sync("https://example.com")

    assert result["accessible"] is True
    assert result["status_code"] == status
    assert result["method"] == "HEAD"
    assert _request_method(opener.call_args) == "HEAD"


@pytest.mark.parametrize("head_status", [403, 405])
def test_check_url_sync_retries_get_for_rejected_head(head_status: int) -> None:
    rejected = HTTPError("https://example.com", head_status, "Rejected", None, None)
    with patch(
        "gpt_researcher.evaluation.link_accessibility.urlopen",
        side_effect=[rejected, _Response(204)],
    ) as opener:
        result = check_url_sync("https://example.com")

    assert result["accessible"] is True
    assert result["method"] == "GET"
    assert [_request_method(call) for call in opener.call_args_list] == ["HEAD", "GET"]


@pytest.mark.parametrize("status", [404, 500])
def test_check_url_sync_marks_http_errors_inaccessible(status: int) -> None:
    error = HTTPError("https://example.com", status, "Error", None, None)
    with patch("gpt_researcher.evaluation.link_accessibility.urlopen", side_effect=error):
        result = check_url_sync("https://example.com")

    assert result["accessible"] is False
    assert result["status_code"] == status
    assert result["failure_reason"] == "http_status"


@pytest.mark.parametrize("status", [304, 307])
def test_check_url_sync_accepts_3xx_reported_as_http_error(status: int) -> None:
    error = HTTPError("https://example.com", status, "Redirect", None, None)
    with patch("gpt_researcher.evaluation.link_accessibility.urlopen", side_effect=error):
        result = check_url_sync("https://example.com")

    assert result["accessible"] is True
    assert result["status_code"] == status
    assert result["method"] == "HEAD"
    assert result["failure_reason"] == ""


@pytest.mark.parametrize("status", [304, 404, 500])
def test_check_url_sync_closes_direct_http_error_resources(status: int) -> None:
    response_body = io.BytesIO(b"response")
    error = HTTPError("https://example.com", status, "Response", None, response_body)
    with patch("gpt_researcher.evaluation.link_accessibility.urlopen", side_effect=error):
        check_url_sync("https://example.com")

    assert response_body.closed is True


@pytest.mark.parametrize("status", [403, 405])
def test_check_url_sync_closes_head_error_before_get_fallback(status: int) -> None:
    head_body = io.BytesIO(b"head")
    get_body = io.BytesIO(b"get")
    head_error = HTTPError("https://example.com", status, "Rejected", None, head_body)
    get_error = HTTPError("https://example.com", 500, "Server error", None, get_body)
    with patch(
        "gpt_researcher.evaluation.link_accessibility.urlopen",
        side_effect=[head_error, get_error],
    ):
        check_url_sync("https://example.com")

    assert head_body.closed is True
    assert get_body.closed is True


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (URLError("timed out"), "timeout"),
        (URLError(socket.gaierror("name or service not known")), "dns_or_host"),
        (URLError(ssl.SSLCertVerificationError("certificate verify failed")), "ssl_certificate"),
        (URLError("connection refused"), "connection"),
        (URLError("actively refused"), "connection"),
        (
            URLError(
                OSError(
                    10061,
                    "No connection could be made because the target machine actively refused it",
                )
            ),
            "connection",
        ),
    ],
)
def test_check_url_sync_classifies_network_errors(error: URLError, reason: str) -> None:
    with patch("gpt_researcher.evaluation.link_accessibility.urlopen", side_effect=error):
        result = check_url_sync("https://example.com")

    assert result["accessible"] is False
    assert result["failure_reason"] == reason


def test_check_url_sync_classifies_invalid_url_without_network_request() -> None:
    result = check_url_sync("https://")

    assert result["accessible"] is False
    assert result["failure_reason"] == "invalid_url_encoding"
    assert result["method"] == "normalize"


def test_evaluate_link_accessibility_deduplicates_and_preserves_input_order() -> None:
    calls: list[str] = []

    def checker(url: str) -> dict[str, object]:
        calls.append(url)
        if url.endswith("slow"):
            time.sleep(0.03)
        return {"url": url, "accessible": True, "ssl_verified": True, "failure_reason": ""}

    result = asyncio.run(
        evaluate_link_accessibility(
            "https://example.com/slow https://example.org/fast https://example.com/slow",
            checker=checker,
        )
    )

    assert sorted(calls) == ["https://example.com/slow", "https://example.org/fast"]
    assert [item["url"] for item in result["results"]] == [
        "https://example.com/slow",
        "https://example.org/fast",
    ]
    assert result["total_urls"] == 2
    assert result["accessibility_rate"] == 1.0


def test_evaluate_link_accessibility_handles_no_urls_and_zero_max_urls() -> None:
    no_urls = asyncio.run(evaluate_link_accessibility("no public links"))
    zero_urls = asyncio.run(
        evaluate_link_accessibility(
            "https://example.com/a https://example.com/b", max_urls=0
        )
    )

    assert no_urls["total_urls"] == 0
    assert no_urls["accessibility_rate"] is None
    assert zero_urls["total_urls"] == 2
    assert zero_urls["checked_urls"] == 0
    assert zero_urls["skipped_urls"] == 2


def test_evaluate_link_accessibility_limits_checks_and_converts_checker_exception() -> None:
    calls: list[str] = []

    def checker(url: str) -> dict[str, object]:
        calls.append(url)
        if url.endswith("bad"):
            raise RuntimeError("unexpected checker failure")
        return {"url": url, "accessible": True, "ssl_verified": False, "failure_reason": ""}

    result = asyncio.run(
        evaluate_link_accessibility(
            "https://example.com/good https://example.com/bad https://example.com/skipped",
            max_urls=2,
            checker=checker,
        )
    )

    assert calls == ["https://example.com/good", "https://example.com/bad"]
    assert result["total_urls"] == 3
    assert result["checked_urls"] == 2
    assert result["skipped_urls"] == 1
    assert result["accessible_urls"] == 1
    assert result["failed_urls"] == 1
    assert result["ssl_unverified_accessible_urls"] == 1
    assert result["results"][1]["failure_reason"] == "checker_exception"
