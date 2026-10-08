"""Bocha AI web search retriever."""

import os
import re
import requests
import logging

from ..search_diagnostics import record_search


class BoChaSearch():
    """
    Bocha AI web search retriever.
    """

    def __init__(self, query, headers=None, topic="general", query_domains=None):
        """
        Initializes the BoChaSearch object.

        Args:
            query: Search query.
            headers: Optional request headers from the caller.
            topic: Kept for compatibility with other retrievers.
            query_domains: Optional domain allow-list.
        """
        self.query = query
        self.headers = headers or {}
        self.topic = topic
        self.query_domains = query_domains or None
        self.base_url = os.getenv("BOCHA_SEARCH_URL", "https://api.bochaai.com/v1/web-search")
        self.api_key = self.get_api_key()
        self.logger = logging.getLogger(__name__)

    def get_api_key(self) -> str:
        api_key = self.headers.get("bocha_api_key") or os.getenv("BOCHA_API_KEY", "")
        if not api_key:
            record_search("bocha", self.query, "failed", message="博查API密钥未配置")
        return api_key

    def search(self, max_results=7) -> list[dict[str, str]]:
        """Search the web and normalize results to GPT Researcher format."""
        if not self.api_key:
            return []

        query = self._with_domain_filter(self.query)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "query": query,
            "freshness": os.getenv("BOCHA_FRESHNESS", "noLimit"),
            "summary": os.getenv("BOCHA_SUMMARY", "true").lower() in {"1", "true", "yes", "on"},
            "count": max(1, min(int(max_results or 7), 50)),
        }

        try:
            response = requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=float(os.getenv("BOCHA_REQUEST_TIMEOUT_SECONDS", "30")),
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            message = self._status_message(status, type(exc).__name__)
            record_search("bocha", query, "failed", http_status=status, message=message)
            self.logger.warning("Bocha search failed: %s", message)
            return []

        results = data.get("data", {}).get("webPages", {}).get("value", []) or []
        normalized = []
        for result in results:
            url = result.get("url") or result.get("displayUrl")
            if not url:
                continue
            body = result.get("summary") or result.get("snippet") or result.get("description") or ""
            normalized.append({
                "title": result.get("name") or result.get("title") or "",
                "href": url,
                "body": body,
                "published_date": result.get('datePublished') or result.get('publishedDate') or '',
            })

        record_search("bocha", query, "ok" if normalized else "empty", count=len(normalized))
        return normalized

    def _with_domain_filter(self, query: str) -> str:
        if not self.query_domains:
            return query
        domains = [str(domain).strip() for domain in self.query_domains if str(domain).strip()]
        if not domains:
            return query
        if any("site:" in query_part.lower() for query_part in query.split()):
            return query
        domain_expr = " OR ".join(f"site:{domain}" for domain in domains[:8])
        return f"{query} ({domain_expr})"

    @staticmethod
    def _status_message(status: int | None, exc_name: str) -> str:
        messages = {
            400: "博查搜索请求参数错误",
            401: "博查API密钥无效或未授权",
            403: "博查搜索服务拒绝访问",
            429: "博查搜索请求频率或额度受限",
            500: "博查搜索服务端异常",
            502: "博查搜索网关异常",
            503: "博查搜索服务暂不可用",
        }
        if status in messages:
            return messages[status]
        if exc_name == "JSONDecodeError" or re.search("JSON", exc_name, re.I):
            return "博查搜索响应不是有效JSON"
        return f"博查搜索连接或响应异常（{exc_name}）"
