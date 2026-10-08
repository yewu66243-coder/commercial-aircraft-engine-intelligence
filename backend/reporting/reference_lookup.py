"""Conservative title-based metadata lookup for bibliography formatting."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List
from urllib.parse import urlsplit
from .reference_metadata import missing_reference_fields


TRUSTED_METADATA_DOMAINS = (
    "cnki.net", "wanfangdata.com.cn", "cqvip.com", "doi.org",
    "crossref.org", "semanticscholar.org", "openalex.org",
    "patents.google.com", "worldwide.espacenet.com", "wipo.int",
    "pss-system.cponline.cnipa.gov.cn", "cnipa.gov.cn",
)


def _cache_path() -> Path:
    return Path(os.getenv(
        "REFERENCE_METADATA_CACHE_PATH",
        Path.cwd() / "local_docs" / "reference_metadata_cache.json",
    ))


def _normalize(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _clean_title(value: str) -> str:
    title = re.sub(r"\s*\.(?:pdf|docx?|txt|md|xlsx?)$", "", str(value or ""), flags=re.I)
    title = title.replace("\\_", "_")
    title = re.sub(r"[_\-]\s*(?:[A-Z][A-Za-z]+\s+[A-Z][A-Za-z]+|[A-Z][A-Za-z]+)$", "", title)
    title = re.sub(r"[_\-]\s*(?:本报记者|记者|通讯员|作者)$", "", title)
    title = re.sub(r"\s+", " ", title.replace("_", " ")).strip(" -_。.，,;；")
    return title


def _title_match_score(expected: str, candidate: str) -> float:
    left, right = _normalize(expected), _normalize(candidate)
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return min(len(left), len(right)) / max(len(left), len(right))
    left_tokens = set(left[i:i + 2] for i in range(max(1, len(left) - 1)))
    right_tokens = set(right[i:i + 2] for i in range(max(1, len(right) - 1)))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _load_cache() -> dict[str, Any]:
    path = _cache_path()
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _save_cache(cache: dict[str, Any]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _trusted_score(url: str) -> float:
    lower = (urlsplit(str(url or "")).hostname or '').lower()
    if any(lower == domain or lower.endswith('.' + domain) for domain in TRUSTED_METADATA_DOMAINS):
        return 0.2
    if re.search(r"scholar|journal|periodical|patent|专利|期刊|论文", lower, re.I):
        return 0.08
    return 0.0


def _search_with_project_retriever(query: str, max_results: int) -> list[dict[str, str]]:
    provider = os.getenv("REFERENCE_LOOKUP_PROVIDER", "duckduckgo").strip().lower()
    if provider in {"", "none", "off", "disabled"}:
        return []
    try:
        if provider == "bing":
            from gpt_researcher.retrievers.bing.bing import BingSearch
            raw = BingSearch(query).search(max_results=max_results)
        else:
            # The general retriever suppresses exceptions; bibliography repair needs them.
            from ddgs import DDGS
            raw = DDGS(timeout=8).text(query, region='wt-wt', max_results=max_results)
    except Exception as exc:
        raise RuntimeError(f'reference_search:{type(exc).__name__}') from exc
    results: list[dict[str, str]] = []
    for item in raw or []:
        results.append({
            "title": str(item.get("title") or item.get("name") or ""),
            "url": str(item.get("href") or item.get("url") or ""),
            "snippet": str(item.get("body") or item.get("snippet") or item.get("content") or ""),
        })
    return results


def _extract_journal(text: str) -> str:
    patterns = [
        r"[《<]([^》<>]{2,40})[》>]\s*(?:\d{4}|第|\()",
        r"(?:来源|刊名|期刊)[:：]\s*([^,，。；;]{2,40})",
        r"\b([A-Z][A-Za-z& ]{4,60}(?:Journal|Review|Magazine|Week|News|Aviation Week)[A-Za-z& ]*)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match[1].strip(" -_，,。")
    return ""


def _extract_pages_or_issue(text: str) -> str:
    issue = ""
    match = re.search(r"(\d{4})\s*[,，]\s*(\d+)\s*(?:\(([^)）]+)\))?\s*[:：]\s*([\d\-–—]+)", text)
    if match:
        return (f"{match[2]}({match[3]}):{match[4]}" if match[3] else f"{match[2]}:{match[4]}").replace("–", "-").replace("—", "-")
    match = re.search(r"(?:第)?(\d{1,4})\s*(?:期|卷)\s*[:：]?\s*([\d\-–—]+)?", text)
    if match:
        issue = f"({match[1]})"
        if match[2]:
            issue += f":{match[2].replace('–', '-').replace('—', '-')}"
    return issue


def _extract_metadata_from_result(record: dict[str, Any], result: dict[str, str]) -> dict[str, Any]:
    text = " ".join([result.get("title", ""), result.get("snippet", ""), result.get("url", "")])
    expected = _clean_title(record.get("title") or record.get("description") or record.get("file_name") or "")
    candidate_title = re.split(r'\s+[|｜]\s*', result.get('title', ''))[0]
    score = _title_match_score(expected, candidate_title)
    if score < float(os.getenv("REFERENCE_LOOKUP_MIN_SCORE", "0.72")):
        return {}
    year_match = re.search(r"(?:出版|发表|发布时间|年份|年限)\s*[:：]?\s*((?:19|20)\d{2})", result.get('snippet', ''))
    doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, re.I)
    patent_match = re.search(r"\b(?:CN|US|EP|WO)\s?\d{4,}[A-Z]?\b", text, re.I)
    journal = _extract_journal(text)
    volume_issue_pages = _extract_pages_or_issue(text)
    return {
        "lookup_title": expected,
        "container": journal,
        "year": year_match[1] if year_match else "",
        "volume_issue_pages": volume_issue_pages,
        "doi": doi_match[0] if doi_match else "",
        "patent_number": patent_match[0].replace(" ", "") if patent_match else "",
        "lookup_url": result.get("url", ""),
        "lookup_source_title": result.get("title", ""),
        "lookup_confidence": round(min(score, 0.99), 3),
    }


def _detail_metadata(url, expected_title):
    """Read structured publisher metadata only after verifying the article title."""
    if _trusted_score(url) != 0.2 or urlsplit(url).scheme not in {'http', 'https'}:
        return {}
    import requests
    from bs4 import BeautifulSoup
    with requests.get(url, timeout=(4, 8), stream=True, allow_redirects=False) as response:
        response.raise_for_status()
        if response.is_redirect:
            return {}
        data = bytearray()
        for chunk in response.iter_content(16384):
            data.extend(chunk)
            if len(data) > 2_000_000:
                return {}
    soup = BeautifulSoup(bytes(data), 'html.parser')
    tags = {}
    for tag in soup.find_all('meta'):
        key = str(tag.get('name') or tag.get('property') or '').lower()
        value = tag.get('content')
        if value:
            tags.setdefault(key, []).append(value.strip())
    title = next(iter(tags.get('citation_title', [])), '')
    if _title_match_score(expected_title, title) < 0.85:
        return {}
    fields = {'container': 'citation_journal_title', 'volume': 'citation_volume',
              'issue': 'citation_issue', 'doi': 'citation_doi',
              'publication_date': 'citation_publication_date'}
    output = {key: tags[tag][0] for key, tag in fields.items() if tags.get(tag)}
    if tags.get('citation_author'):
        output['author'] = '; '.join(tags['citation_author'])
    date = output.get('publication_date', '')
    if re.match(r'^(19|20)\d{2}', date):
        output['year'] = date[:4]
    first = next(iter(tags.get('citation_firstpage', [])), '')
    last = next(iter(tags.get('citation_lastpage', [])), '')
    if first:
        output['pages'] = first + ('-' + last if last and last != first else '')
    output['metadata_provenance'] = {key: {'url': url, 'method': 'citation_meta'} for key in output}
    return output


def resolve_reference_metadata(record: dict[str, Any],
                               *,
                               searcher: Callable[[str, int], Iterable[dict[str, str]]] | None = None,
                               force: bool = False) -> dict[str, Any]:
    """Return extra metadata only when title search yields a reliable match."""
    enabled = os.getenv("REFERENCE_LOOKUP_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    if not force and not enabled and searcher is None:
        return {}
    source_type = str(record.get("source_type") or "")
    has_named_local_source = bool(record.get("title") or record.get("file_name"))
    if not has_named_local_source and record.get("url") and not re.search(r"论文|期刊|专利|patent|journal", source_type, re.I):
        return {}
    title = _clean_title(record.get("title") or record.get("file_name") or record.get("description") or "")
    if re.match(r"^https?://", title, re.I):
        return {}
    if len(_normalize(title)) < 6:
        return {}
    query_tail = " 专利" if re.search(r"专利|patent", source_type, re.I) else " 期刊 论文"
    author = str(record.get("author") or "").strip()
    for part in re.split(r'[;；,，]', author):
        if part.strip():
            title = re.sub(rf'\s*{re.escape(part.strip())}$', '', title).strip()
    query = f'"{title}" {author}{query_tail}'.strip()
    key = _normalize(query)
    cache = _load_cache()
    ttl = int(os.getenv("REFERENCE_LOOKUP_CACHE_TTL_SECONDS", str(30 * 24 * 3600)))
    cached = cache.get(key)
    if cached and cached.get('version') == 3 and time.time() - float(cached.get("time", 0)) < (ttl if cached.get('status') == 'matched' else 300):
        return dict(cached.get("metadata") or {})
    journal = str(record.get('container') or record.get('journal') or '').strip()
    queries = [query, f'"{title}" {journal}'.strip(),
               f'"{title}" {journal} 目录 年 期 页码'.strip()]
    attempts, candidates, errors, seen = [], [], [], set()
    for search_query in dict.fromkeys(queries):
        attempt = {'query': search_query, 'candidates': []}
        attempts.append(attempt)
        try:
            results = list((searcher or _search_with_project_retriever)(search_query, int(os.getenv('REFERENCE_LOOKUP_MAX_RESULTS', '5'))) or [])
        except Exception as exc:
            attempt['error'] = str(exc)[:160]
            errors.append(attempt['error'])
            continue
        for result in results:
            candidate = _extract_metadata_from_result({**record, 'title': title}, result)
            trace = {'title': result.get('title', ''), 'url': result.get('url', ''),
                     'reason': 'title_mismatch' if not candidate else 'title_matched'}
            attempt['candidates'].append(trace)
            if not candidate:
                continue
            identity = (result.get('url', ''), result.get('title', ''))
            if identity not in seen and searcher is None:
                try:
                    detail = _detail_metadata(candidate['lookup_url'], title)
                    candidate.update(detail)
                    trace['detail_status'] = 'extracted' if detail else 'no_verified_metadata'
                except Exception as exc:
                    trace['detail_error'] = type(exc).__name__
            seen.add(identity)
            conflicts = [field for field in ('container', 'year', 'doi', 'patent_number')
                         if record.get(field) and candidate.get(field)
                         and _normalize(str(record[field])) != _normalize(str(candidate[field]))]
            if conflicts:
                trace['reason'] = 'conflicting_metadata'
                trace['conflicts'] = conflicts
                continue
            merged = {**candidate, **{k: v for k, v in record.items() if v}}
            trace['missing_fields'] = missing_reference_fields(merged)
            candidates.append(candidate)
        if candidates and any(not missing_reference_fields({**c, **{k: v for k, v in record.items() if v}}) for c in candidates):
            break
    metadata = min(candidates, key=lambda c: (
        len(missing_reference_fields({**c, **{k: v for k, v in record.items() if v}})),
        -c.get('lookup_confidence', 0)), default={})
    useful = any(metadata.get(field) for field in ('container', 'year', 'doi', 'patent_number', 'pages'))
    complete = useful and not missing_reference_fields({**metadata, **{k: v for k, v in record.items() if v}})
    status = 'matched' if complete else ('partial' if useful else ('error' if errors else ('no_match' if any(a['candidates'] for a in attempts) else 'no_results')))
    metadata['lookup_status'] = status
    metadata['lookup_attempts'] = attempts
    if errors:
        metadata['lookup_error'] = '; '.join(errors)
    if status == 'error':
        return metadata
    cache[key] = {"version": 3, "time": time.time(), "status": status, "metadata": metadata}
    _save_cache(cache)
    return metadata
