"""Bounded evidence transport and structural depth checks, not fact verification."""
from __future__ import annotations

import re
from itertools import zip_longest

_EXCLUDED = re.compile(r'摘要|关键词|目录|参考文献|证据来源|References|附录|内部核验|实体.*清单|运行统计|本次精读', re.I)
_FRONT_BACK = re.compile(r'引言|前言|背景|研究方法|资料来源|讨论|局限|结论|建议|总结')
_CITATION = re.compile(r'\[(?:(?:原文|URL|文献|来源)\s*\d+|\d+)\]', re.I)


def _excerpt_item(item):
    if isinstance(item, str):
        return '', item.strip()
    if not isinstance(item, dict):
        return '', ''
    metadata = item.get('metadata') or {}
    locator = item.get('url') or item.get('source') or metadata.get('source') or ''
    title = item.get('title') or metadata.get('title') or ''
    page = item.get('page') or metadata.get('page')
    header = f'资料：{str(title)[:120]}；来源定位：{locator}'
    if page is not None:
        header += f'；原始页码字段：{page}'
    text = item.get('raw_content') or item.get('content') or item.get('page_content') or item.get('text') or ''
    return header, str(text).strip()


def pack_evidence(sections, budget=18000):
    """Share the prompt budget across topics and locators; retain originals in audit."""
    groups = []
    total = 0
    for index, section in enumerate(sections, 1):
        entries, seen = [], set()
        context = section.get('context') or []
        if not isinstance(context, list):
            context = [context]
        sources = section.get('sources') or []
        if not isinstance(sources, list):
            sources = [sources]
        for item in (item for pair in zip_longest(context, sources) for item in pair if item is not None):
            header, text = _excerpt_item(item)
            key = re.sub(r'\s+', '', header + text)
            if not key or key in seen:
                continue
            seen.add(key)
            entries.append((header, text))
            total += len(header) + len(text) + 2
        if entries:
            groups.append((f'研究子题 {index} 的资料摘录', entries))
    if not groups or budget <= 0:
        return {'text': '', 'truncated': bool(groups), 'source_entries': 0}
    quota = max(0, budget // len(groups) - 2)
    rendered, count, clipped = [], 0, False
    for label, entries in groups:
        # Use fewer useful passages instead of reducing every source to a
        # title and dropping all locators when many candidates were retrieved.
        maximum_entries = max(1, (quota - len(label) - 2) // 600)
        if len(entries) > maximum_entries:
            entries = entries[:maximum_entries]
            clipped = True
        entry_quota = max(0, (quota - len(label) - 2) // len(entries) - 2)
        blocks = [label]
        for header, text in entries:
            if len(header) + 2 >= entry_quota:
                clipped = True
                continue  # Do not cut a source URL into an invalid locator.
            room = entry_quota - len(header) - 2
            excerpt = text[:room]
            if len(text) > room:
                clipped = True
            blocks.append((header + '\n' + excerpt).strip())
            count += 1
        rendered.append('\n\n'.join(blocks))
    output = '\n\n'.join(rendered)
    return {'text': output, 'truncated': clipped or total > budget, 'source_entries': count}


def _sections(markdown):
    sections = []
    for match in re.finditer(r'^##[ \t]+([^\n]+)\n(.*?)(?=^##[ \t]+|\Z)', markdown or '', re.M | re.S):
        title = re.sub(r'^\d+(?:\.\d+)*[.、．]?\s*', '', match[1].strip())
        sections.append((title, match[2]))
    return sections


def _paragraphs(body):
    body = re.sub(r'```.*?```', '', body, flags=re.S)
    # Different figures can legitimately share the same source and page.
    body = re.sub(r'^[ \t]*\*{0,2}(?:图源|图片来源)\s*[:：].*$', '', body, flags=re.M)
    body = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', body)
    body = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', body)
    body = re.sub(r'<[^>]*>', '', body)
    body = _CITATION.sub('', body)
    body = re.sub(r'https?://[^\s<>]+', '', body)
    body = re.sub(r'^\s*(?:#{1,6}\s|\|).*$', '', body, flags=re.M)
    for paragraph in re.split(r'\n\s*\n', body):
        text = ''.join(re.findall(r'[\u4e00-\u9fffA-Za-z0-9]', paragraph))
        if text:
            yield text


def review_content(markdown, report_type='research_report'):
    minimum = {'research_report': 2500, 'detailed_report': 5000, 'resource_report': 3000}.get(report_type, 2500)
    section_minimum = 500 if report_type == 'detailed_report' else 350
    expected_themes = 3 if report_type == 'detailed_report' else 2
    seen, duplicates, count, themes, thin = set(), 0, 0, 0, []
    placeholders = 0
    for title, body in _sections(markdown):
        if _EXCLUDED.search(title):
            continue
        placeholders += len(re.findall(r'\[(?:URL\s*\?|原文\s*\?|材料引述|来源待补)\]', body, re.I))
        section_count = 0
        for paragraph in _paragraphs(body):
            if len(paragraph) >= 12 and paragraph in seen:
                duplicates += 1
                continue
            seen.add(paragraph)
            count += len(paragraph)
            section_count += len(paragraph)
        if not _FRONT_BACK.search(title):
            themes += 1
            if section_count < section_minimum:
                thin.append(title)
    warnings = []
    if count < minimum:
        warnings.append(f'正文有效内容约{count}字，低于当前报告类型建议的{minimum}字；需结合可用证据补充分析。')
    if themes < expected_themes:
        warnings.append(f'专题分析仅{themes}章，研究问题的覆盖范围需要复核。')
    if thin:
        warnings.append('以下专题展开较少：' + '、'.join(thin[:6]) + '。')
    if duplicates:
        warnings.append(f'发现{duplicates}段重复内容，建议合并并补充不同证据或分析。')
    if placeholders:
        warnings.append(f'存在{placeholders}处尚未定位的来源占位标记；须用已有真实来源补齐，无法定位的具体断言移入内部核验记录。')
    return {'body_characters': count, 'suggested_minimum': minimum,
            'thematic_sections': themes, 'thin_sections': thin,
            'duplicate_paragraphs': duplicates, 'warnings': warnings,
            'unresolved_placeholders': placeholders,
            'needs_enrichment': bool(warnings), 'check_kind': 'structural_heuristics_not_fact_verification'}


def usable_revision(original, revision):
    """Reject revisions that erase chapters, citations or source locators."""
    from .citations import CitationRegistry
    if not revision.strip():
        return False
    before = review_content(original)
    after = review_content(revision)
    if after['body_characters'] < before['body_characters']:
        return False
    titles = {title for title, _ in _sections(original) if not _EXCLUDED.search(title)}
    if not titles.issubset({title for title, _ in _sections(revision)}):
        return False
    revised_sections = dict(_sections(revision))
    for title, body in _sections(original):
        if _EXCLUDED.search(title):
            continue
        revised_body = revised_sections.get(title, '')
        if not set(_CITATION.findall(body)).issubset(set(_CITATION.findall(revised_body))):
            return False
        original_size = sum(len(p) for p in set(_paragraphs(body)))
        revised_size = sum(len(p) for p in set(_paragraphs(revised_body)))
        if revised_size < original_size * 0.85:
            return False
    if not set(_CITATION.findall(original)).issubset(set(_CITATION.findall(revision))):
        return False
    original_sources = CitationRegistry(original).definitions
    revised_sources = CitationRegistry(revision).definitions
    for label, source in original_sources.items():
        revised = revised_sources.get(label, {})
        for field in ('url', 'file_name'):
            if source.get(field) and source[field] != revised.get(field):
                return False
    locators = re.findall(r'https?://[^\s<>\[\]()，。；]+|/outputs/report_images/[^\s)]+', original)
    return all(locator in revision for locator in locators)


def bind_local_source_filenames(markdown, sources):
    """Restore omitted filenames only when a complete known title is unambiguous."""
    from .citations import DEFINITION_RE, source_dict
    catalog = [source_dict(source) for source in sources]
    output, in_references = [], False
    for line in markdown.splitlines(keepends=True):
        if re.match(r'^#{1,6}\s', line):
            in_references = bool(re.search(r'参考文献|证据来源|References', line, re.I))
        match = DEFINITION_RE.match(line) if in_references else None
        if match and match[1].startswith('[原文'):
            description = match[2]
            candidates = []
            if not re.search(r'\.(?:pdf|docx?|txt|md)\b', description, re.I):
                for source in catalog:
                    title = str(source.get('title') or '')
                    author = str(source.get('author') or '').strip()
                    if author:
                        title = title.removesuffix('_' + author)
                    filename = str(source.get('file_name') or '')
                    full_title = r'(?:^|[.．。；;《]\s*)' + re.escape(title) + r'(?=\s*(?:[.．。；;》]|$))'
                    if filename and len(title) >= 4 and re.search(full_title, description):
                        candidates.append(filename)
            if len(set(candidates)) == 1:
                line = line[:match.start(2)] + candidates[0] + ' ' + description + ('\n' if line.endswith('\n') else '')
        output.append(line)
    return ''.join(output)
