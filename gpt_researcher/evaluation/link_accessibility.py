"""Reusable public-URL accessibility checks for generated reports."""

from __future__ import annotations

import asyncio
import re
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen


UrlCheckResult = dict[str, object]
UrlChecker = Callable[[str], UrlCheckResult]

_URL_TERMINATORS = set(' \t\r\n<>"\'`()|[]{}，。；;、（）】》”’')
_TRAILING_URL_PUNCTUATION = ".,;:!?。；，、)]}）】》"


def clean_url_candidate(url: str) -> str:
    cleaned = (url or "").strip().strip('<>"\'`').rstrip(_TRAILING_URL_PUNCTUATION)
    if not cleaned.startswith(("http://", "https://")):
        return ""
    try:
        parsed = urlsplit(cleaned)
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.netloc or not parsed.hostname:
        return ""
    if any(character in parsed.netloc for character in "，。；：、（）【】《》"):
        return ""
    return cleaned


def extract_public_urls(report: str) -> list[str]:
    """Return distinct HTTP(S) URLs in first-appearance order."""
    urls: list[str] = []
    seen: set[str] = set()
    text = report or ""
    for match in re.finditer(r"https?://", text):
        start = match.start()
        end = start
        while end < len(text) and text[end] not in _URL_TERMINATORS:
            end += 1
        url = clean_url_candidate(text[start:end])
        if url and url not in seen:
            urls.append(url)
            seen.add(url)
    return urls


def normalize_url_for_request(url: str) -> str:
    """Normalize a public URL without weakening TLS verification."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValueError("invalid public HTTP(S) URL")
    netloc = parsed.netloc.encode("idna").decode("ascii")
    path = quote(parsed.path or "/", safe="/%:@-._~!$&'()*+,;=")
    query = quote(parsed.query, safe="=&?/%:@-._~!$'()*+,;")
    return urlunsplit((parsed.scheme, netloc, path, query, ""))


def classify_url_error(error: str, status_code: int | None = None) -> str:
    """Map request failures to stable, display-safe categories."""
    lower_error = (error or "").lower()
    if status_code is not None:
        return "http_status"
    if "certificate_verify_failed" in lower_error or "certificate verify failed" in lower_error or "ssl" in lower_error:
        return "ssl_certificate"
    if "timed out" in lower_error or "timeout" in lower_error:
        return "timeout"
    if "codec can't encode" in lower_error or "ordinal not in range" in lower_error:
        return "invalid_url_encoding"
    if "no host" in lower_error or "name or service not known" in lower_error or "getaddrinfo" in lower_error:
        return "dns_or_host"
    if any(
        marker in lower_error
        for marker in (
            "connection refused",
            "actively refused",
            "winerror 10061",
            "errno 10061",
            "connection reset",
            "connection aborted",
            "connection error",
            "remote end closed",
            "network is unreachable",
        )
    ):
        return "connection"
    if "invalid" in lower_error or "unknown url type" in lower_error:
        return "invalid_url"
    return "network_or_unknown"


def _failure_result(
    url: str,
    *,
    checked_url: str,
    method: str,
    error: str,
    failure_reason: str,
    ssl_verified: bool | None,
    status_code: int | None = None,
) -> UrlCheckResult:
    return {
        "url": url,
        "checked_url": checked_url,
        "status_code": status_code,
        "accessible": False,
        "method": method,
        "ssl_verified": ssl_verified,
        "error": error,
        "failure_reason": failure_reason,
        "warning": "",
    }


def check_url_sync(url: str, timeout: int = 6) -> UrlCheckResult:
    """Synchronously check an HTTP(S) URL, retrying GET when HEAD is rejected."""
    headers = {"User-Agent": "Mozilla/5.0 Intelligence-System-URL-Check"}
    original_url = url
    try:
        checked_url = normalize_url_for_request(url)
    except Exception as exc:
        return _failure_result(
            original_url,
            checked_url=url,
            method="normalize",
            error=str(exc),
            failure_reason="invalid_url_encoding",
            ssl_verified=None,
        )

    method = "HEAD"
    while True:
        try:
            request = Request(checked_url, headers=headers, method=method)
            with urlopen(request, timeout=timeout) as response:
                status_code = int(getattr(response, "status", 0) or response.getcode())
            accessible = 200 <= status_code < 400
            return {
                "url": original_url,
                "checked_url": checked_url,
                "status_code": status_code,
                "accessible": accessible,
                "method": method,
                "ssl_verified": True,
                "error": "",
                "failure_reason": "" if accessible else "http_status",
                "warning": "",
            }
        except HTTPError as exc:
            status_code = int(exc.code)
            if 300 <= status_code < 400:
                return {
                    "url": original_url,
                    "checked_url": checked_url,
                    "status_code": status_code,
                    "accessible": True,
                    "method": method,
                    "ssl_verified": True,
                    "error": "",
                    "failure_reason": "",
                    "warning": "",
                }
            if method == "HEAD" and status_code in {403, 405}:
                method = "GET"
                continue
            return _failure_result(
                original_url,
                checked_url=checked_url,
                method=method,
                error=str(exc),
                failure_reason="http_status",
                ssl_verified=True,
                status_code=status_code,
            )
        except URLError as exc:
            error = str(exc.reason)
            return _failure_result(
                original_url,
                checked_url=checked_url,
                method=method,
                error=error,
                failure_reason=classify_url_error(error),
                ssl_verified=True,
            )
        except Exception as exc:
            error = str(exc)
            return _failure_result(
                original_url,
                checked_url=checked_url,
                method=method,
                error=error,
                failure_reason=classify_url_error(error),
                ssl_verified=True,
            )


def _checker_exception_result(url: str, error: Exception) -> UrlCheckResult:
    return _failure_result(
        url,
        checked_url=url,
        method="checker",
        error=str(error),
        failure_reason="checker_exception",
        ssl_verified=None,
    )


async def evaluate_link_accessibility(
    report: str,
    max_urls: int | None = None,
    checker: UrlChecker = check_url_sync,
) -> dict[str, object]:
    """Evaluate extracted links concurrently using ``checker(url)`` exactly once each."""
    urls = extract_public_urls(report)
    check_limit = len(urls) if max_urls is None else max(0, int(max_urls))
    checked_urls = urls[:check_limit]
    skipped_count = len(urls) - len(checked_urls)

    if checked_urls:
        raw_results = await asyncio.gather(
            *(asyncio.to_thread(checker, url) for url in checked_urls),
            return_exceptions=True,
        )
        results: list[UrlCheckResult] = []
        for url, result in zip(checked_urls, raw_results):
            if isinstance(result, Exception):
                results.append(_checker_exception_result(url, result))
            elif isinstance(result, dict):
                results.append(result)
            else:
                results.append(
                    _checker_exception_result(url, TypeError("checker must return a dict"))
                )
    else:
        results = []

    accessible_count = sum(1 for item in results if item.get("accessible"))
    failed_count = len(results) - accessible_count
    ssl_unverified_count = sum(
        1
        for item in results
        if item.get("accessible") and item.get("ssl_verified") is False
    )
    failure_reasons: dict[str, int] = {}
    for item in results:
        reason = item.get("failure_reason") or (
            "accessible" if item.get("accessible") else "network_or_unknown"
        )
        reason_text = str(reason)
        failure_reasons[reason_text] = failure_reasons.get(reason_text, 0) + 1

    return {
        "total_urls": len(urls),
        "checked_urls": len(results),
        "accessible_urls": accessible_count,
        "failed_urls": failed_count,
        "accessibility_rate": round(accessible_count / len(results), 4) if results else None,
        "skipped_urls": skipped_count,
        "ssl_unverified_accessible_urls": ssl_unverified_count,
        "failure_reasons": failure_reasons,
        "results": results,
    }
