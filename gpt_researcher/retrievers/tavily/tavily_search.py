"""Tavily API search retriever for GPT Researcher.

This module provides the TavilySearch class for performing web searches
using the Tavily API.
"""

import json
import os
import hashlib
import re
import time
from typing import Literal, Optional, Sequence

import requests
from ..search_diagnostics import record_search


class TavilySearch:
    """
    Tavily API Retriever
    """

    _unavailable = {}

    def __init__(self, query, headers=None, topic="general", query_domains=None):
        """
        Initializes the TavilySearch object.

        Args:
            query (str): The search query string.
            headers (dict, optional): Additional headers to include in the request. Defaults to None.
            topic (str, optional): The topic for the search. Defaults to "general".
            query_domains (list, optional): List of domains to include in the search. Defaults to None.
        """
        self.query = query
        self.headers = headers or {}
        self.topic = topic
        self.base_url = "https://api.tavily.com/search"
        self.api_key = self.get_api_key()
        self.headers = {
            "Content-Type": "application/json",
        }
        self.query_domains = query_domains or None

    def get_api_key(self):
        """
        Gets the Tavily API key
        Returns:

        """
        api_key = self.headers.get("tavily_api_key")
        if not api_key:
            try:
                api_key = os.environ["TAVILY_API_KEY"]
            except KeyError:
                print(
                    "Tavily API key not found, set to blank. If you need a retriver, please set the TAVILY_API_KEY environment variable."
                )
                return ""
        return api_key


    def _search(
        self,
        query: str,
        search_depth: Literal["basic", "advanced"] = "basic",
        topic: str = "general",
        days: int = 2,
        max_results: int = 10,
        include_domains: Sequence[str] = None,
        exclude_domains: Sequence[str] = None,
        include_answer: bool = False,
        include_raw_content: bool = False,
        include_images: bool = False,
        use_cache: bool = True,
    ) -> dict:
        """
        Internal search method to send the request to the API.
        """

        data = {
            "query": query,
            "search_depth": search_depth,
            "topic": topic,
            "days": days,
            "include_answer": include_answer,
            "include_raw_content": include_raw_content,
            "max_results": max_results,
            "include_domains": include_domains,
            "exclude_domains": exclude_domains,
            "include_images": include_images,
            "api_key": self.api_key,
            "use_cache": use_cache,
        }

        response = requests.post(
            self.base_url, data=json.dumps(data), headers=self.headers, timeout=100
        )

        if response.status_code == 200:
            return response.json()
        else:
            # Raises a HTTPError if the HTTP request returned an unsuccessful status code
            response.raise_for_status()

    def search(self, max_results=10):
        """
        Searches the query
        Returns:

        """
        key_id = hashlib.sha256(self.api_key.encode()).hexdigest()
        blocked = self._unavailable.get(key_id)
        try:
            if blocked and blocked[0] > time.monotonic():
                record_search('tavily', self.query, 'cooldown', http_status=blocked[1], message=blocked[2])
                return self._fallback(max_results)
            # Search the query
            results = self._search(
                self.query,
                search_depth="basic",
                max_results=max_results,
                topic=self.topic,
                include_domains=self.query_domains,
            )
            sources = results.get("results", [])
            if not sources:
                record_search('tavily', self.query, 'empty', message='搜索服务没有返回结果，尝试备用搜索')
                return self._fallback(max_results)
            # Return the results
            search_response = [
                {"href": obj["url"], "body": obj.get("content", ""),
                 "title": obj.get("title", ""), "published_date":obj.get("published_date", ""),
                 "raw_content":obj.get("raw_content") or ""} for obj in sources
            ]
            record_search('tavily', self.query, 'ok', count=len(search_response))
        except Exception as e:
            response = getattr(e, 'response', None)
            code = response.status_code if response is not None else None
            messages = {401: '搜索API密钥无效', 403: '搜索服务拒绝访问',
                        429: '搜索请求频率或额度受限', 432: 'Tavily套餐用量已达上限',
                        433: '搜索服务账户访问受限'}
            message = messages.get(code, '搜索服务连接或响应异常（' + type(e).__name__ + '）')
            if code in {401, 403, 429, 432, 433}:
                self._unavailable[key_id] = (time.monotonic() + 120, code, message)
            record_search('tavily', self.query, 'failed', http_status=code, message=message)
            search_response = self._fallback(max_results)
        return search_response

    def _fallback(self, max_results):
        if os.getenv('WEB_SEARCH_FALLBACK_ENABLED', 'true').lower() not in {'1', 'true', 'yes', 'on'}:
            return []
        try:
            from ddgs import DDGS
        except ImportError:
            record_search('ddgs', self.query, 'failed', message='备用搜索依赖ddgs未安装')
            return []
        # Long Boolean queries generated for another engine often return no matches.
        simplified = re.sub(r'(?:site:|after:|before:)\S+', ' ', self.query)
        simplified = re.sub(r'\b(?:OR|AND)\b|[()\"]', ' ', simplified)
        simplified = re.split(r'[。；;]', simplified)[0]
        simplified = ' '.join(simplified.split()[:18])[:180].strip()
        queries = [simplified]
        if self.query_domains:
            queries.insert(0, simplified + ' (' + ' OR '.join('site:' + d for d in self.query_domains[:3]) + ')')
        for query in dict.fromkeys(queries):
            try:
                results = list(DDGS(timeout=15).text(query, region='wt-wt', max_results=max_results))
                results = [item for item in results if item.get('href') or item.get('url')]
                record_search('ddgs', query, 'ok' if results else 'empty', count=len(results),
                              message='备用搜索；优先站点无结果时扩大公开检索范围')
                if results:
                    return results
            except Exception as exc:
                record_search('ddgs', query, 'failed', message='备用搜索失败（' + type(exc).__name__ + '）')
        return []
