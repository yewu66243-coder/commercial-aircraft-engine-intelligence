"""Human-review subjects come only from the published report, never retrieval results."""
import re
from urllib.parse import urlsplit

import mistune

from .evidence_samples import REF
from .manual_facts import fact_fields, NON_FACT_TYPES
from .review_samples import _evidence


_EXCLUDED = re.compile(r'目录|参考文献|References|证据来源|来源列表|内部核验|附录', re.I)
_LINK = re.compile(r'https?://[^\s<>\[\]{}，。；、！？"\']+', re.I)


def _key(value):
    return re.sub(r'\s+', '', value).casefold()


def _public_url(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {'http', 'https'} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _inline(tokens):
    parts = []
    for token in tokens:
        kind = token['type']
        if kind == 'image':
            continue
        text = _inline(token['children']) if 'children' in token else token.get('raw', '')
        if kind == 'link':
            text = f'[{text}]({token["attrs"]["url"]})'
        elif kind in {'softbreak', 'linebreak'}:
            text = '\n'
        elif kind == 'inline_html':
            text = ''
        parts.append(text)
    return ''.join(parts)


def _urls(text):
    """Use Markdown parsing for link destinations, including balanced parentheses."""
    tokens = mistune.create_markdown(renderer='ast')(text)
    urls = []

    def visit(items):
        for token in items:
            if token['type'] == 'image':
                continue
            if token['type'] == 'link':
                url = token['attrs']['url']
                if _public_url(url):
                    urls.append(url)
                continue
            if token['type'] == 'text':
                for match in _LINK.finditer(token.get('raw', '')):
                    url = match[0].rstrip('.,;:!?')
                    while url.endswith(')') and url.count(')') > url.count('('):
                        url = url[:-1]
                    if _public_url(url):
                        urls.append(url)
            visit(token.get('children', []))

    visit(tokens)
    return list(dict.fromkeys(urls))


def published_units(report):
    """Parse actual paragraphs/list items/table rows; exclude navigation and end matter."""
    tokens = mistune.create_markdown(renderer='ast', plugins=['table'])(report)
    sections, excluded, ordinal = [], None, 0

    def blocks(items):
        for token in items:
            kind = token['type']
            if kind in {'paragraph', 'block_text'}:
                yield _inline(token.get('children', [])), '段落', {}
            elif kind == 'table':
                headers = []
                for part in token['children']:
                    if part['type'] == 'table_head':
                        headers = [_inline(cell['children']) for cell in part['children']]
                    elif part['type'] == 'table_body':
                        for row in part['children']:
                            cells = [_inline(cell['children']) for cell in row['children']]
                            yield (' | '.join(cells), '表格行（' + ' / '.join(headers) + '）',
                                   {'table_headers': headers, 'table_cells': cells})
            elif kind not in {'heading', 'block_code', 'block_html', 'blank_line'}:
                yield from blocks(token.get('children', []))

    for token in tokens:
        if token['type'] == 'heading':
            level = token['attrs']['level']
            title = _inline(token['children']).strip()
            if excluded is not None and level > excluded:
                continue
            excluded = level if _EXCLUDED.search(title) else None
            sections = [(depth, name) for depth, name in sections if depth < level]
            sections.append((level, title))
            continue
        if excluded is not None:
            continue
        for text, kind, fields in blocks([token]):
            if not text.strip() or re.match(r'^\s*(?:图源|图片来源|关键词|\*\*关键词)', text):
                continue
            ordinal += 1
            location = ' / '.join(name for _, name in sections[1:] or sections) or '正文'
            location += f' / {kind} {ordinal}'
            # A table row stays whole so its values retain their column/object context.
            units = [text] if kind.startswith('表格') else re.findall(
                r'.+?(?:[。！？](?:\s*(?:\[\[\d+\]\]\(#ref-[^)]+\)|' + REF.pattern + r'))*|$)',
                text, flags=re.S)
            for unit in units:
                if unit.strip():
                    yield {'claim': unit.strip(), 'body_location': location, **fields}


def _definitions(report, citation_map):
    """A bibliography entry can resolve a body citation, but cannot create a sample."""
    definitions = {}
    reference_level = None
    for line in report.splitlines():
        heading = re.match(r'^(#{1,6})\s+(.+)', line)
        if heading:
            level = len(heading[1])
            if re.search(r'参考文献|References|证据来源|来源列表', heading[2], re.I):
                reference_level = level
            elif reference_level is not None and level <= reference_level:
                reference_level = None
        if reference_level is None:
            continue
        match = re.match(r'^\s*[-*]?\s*(' + REF.pattern + r')\s*(.+)', line)
        if match:
            label, description = match.groups()
            urls = _urls(description)
            definitions[_key(label)] = urls[0] if len(urls) == 1 else description
    for label, source in citation_map.items():
        # Local papers' DOI links are bibliographic metadata, not public web evidence.
        locator = source.get('url') or source.get('file_name') or source.get('description', '')
        printed = definitions.get(_key(label), '')
        if _public_url(printed) and not source.get('file_name'):
            locator = printed
        if not source.get('file_name'):
            urls = _urls(locator)
            if len(urls) == 1:
                locator = urls[0]
        if locator:
            definitions[_key(label)] = locator
    return definitions


def _body_refs(claim):
    refs = []

    def visit(items):
        for token in items:
            if token['type'] == 'image':
                continue
            if token['type'] == 'link':
                anchor = re.fullmatch(r'#ref-(\d+)', token['attrs']['url'])
                if anchor:
                    refs.append(f'[{anchor[1]}]')
                # A numeric label on an external link is not a bibliography pointer.
                continue
            if token['type'] == 'text':
                refs.extend(REF.findall(token.get('raw', '')))
            visit(token.get('children', []))

    visit(mistune.create_markdown(renderer='ast')(claim))
    return list(dict.fromkeys(refs))


def _visible_text(claim):
    # Remove URLs and citation syntax before recognizing names/numbers in visible text.
    tokens = mistune.create_markdown(renderer='ast')(claim)

    def visible(items):
        for token in items:
            if token['type'] in {'image', 'inline_html'}:
                continue
            if token['type'] == 'link' and str(token['attrs']['url']).startswith('#ref-'):
                continue
            if 'children' in token:
                yield from visible(token['children'])
            else:
                yield token.get('raw', '')

    return _LINK.sub('', REF.sub('', ''.join(visible(tokens))))


_STABLE_ID = re.compile(r'(?<![A-Za-z0-9])(?:CAD\d{4}-[A-Za-z0-9-]+|'
                        r'AD\s*\d{4}-\d{2}-\d{2})(?![A-Za-z0-9])', re.I)


def _citation_units(unit, definitions, catalog):
    """Split only explicit citation boundaries; trailing groups stay grouped if ambiguous."""
    claim = unit['claim']
    if unit.get('table_cells'):
        return [(unit, '表格行引用（需核对适用列）')]
    # A citation immediately before a comma/semicolon explicitly closes that clause.
    protected = [(m.start(), m.end()) for m in _LINK.finditer(claim)]
    cuts = [m for m in re.finditer(r'(?<=\))[，,；;](?=\S)|(?<=\])[，,；;](?=\S)|[；;]', claim)
            if not any(start <= m.start() < end for start, end in protected)]
    boundaries = [0] + [m.end() for m in cuts]
    parts = [claim[start:(cuts[index].start() if index < len(cuts) else len(claim))]
             for index, start in enumerate(boundaries)]
    if len(parts) < 2:
        return [(unit, '原句明确引用')]
    refs = _body_refs(claim)
    if all(_body_refs(part) or _urls(part) for part in parts):
        return [({**unit, 'claim': part, 'parent_claim': claim}, '分句明确引用') for part in parts if part.strip()]
    # Identifier alignment is a navigation aid, never an assertion of source correctness.
    aligned = []
    for part in parts:
        identifiers = _STABLE_ID.findall(_visible_text(part))
        matching = []
        if len(identifiers) == 1:
            for ref in refs:
                locator = definitions.get(_key(ref), '')
                saved = [s for s in catalog.get('sources', [])
                         if (s.get('locator') or s.get('file_name')) == locator]
                source_text = '\n'.join(str(s.get('title', '')) + '\n' + '\n'.join(
                    str(page.get('text', '')) for page in s.get('pages', [])) for s in saved)
                source_ids = {_key(value) for value in _STABLE_ID.findall(source_text)}
                if _key(identifiers[0]) in source_ids:
                    matching.append(ref)
        if len(matching) != 1:
            return [(unit, '句末引用组，分句对应待人工确认')]
        aligned.append(({**unit, 'claim': part, 'parent_claim': claim,
                         'assigned_refs': matching}, '唯一文件编号对齐（待人工复核）'))
    return aligned


def extract_published_rows(report, citation_map, catalog, *, audit=None):
    definitions = _definitions(report, citation_map)
    rows, seen_entities, seen_sources, evidence_cache = [], {}, {}, {}

    def excerpts(claim, locators, name=''):
        refs = list(locators)
        key = (claim, tuple(locators.items()), name)
        if key not in evidence_cache:
            evidence_cache[key] = _evidence(claim, refs, locators, catalog, name=name) if refs else []
        return evidence_cache[key]

    units = [part for unit in published_units(report) for part in _citation_units(unit, definitions, catalog)]
    for unit, citation_assignment in units:
        claim = unit['claim']
        refs = unit.get('assigned_refs', _body_refs(claim))
        locators = {ref: definitions[_key(ref)] for ref in refs if definitions.get(_key(ref))}
        direct_urls = _urls(claim)
        for index, url in enumerate(direct_urls, 1):
            locators[f'[直接链接{index}]'] = url
        facts, statement_type = fact_fields(_visible_text(claim), unit.get('table_headers'), unit.get('table_cells'))
        if not facts and audit is not None:
            audit.append({**unit, 'statement_type': statement_type,
                          'reason': '建议/研究问题/研究范围说明不自动计入事实分母，需人工复核' if statement_type in NON_FACT_TYPES
                          else '规则未识别对象或参数；须逐段检查，事实漏项在“漏项与争议”中补充'})
        for fact in facts:
            name = fact['name']
            key = (name, fact['attribute'], fact['value'], fact['unit'], fact['conditions'], _key(_visible_text(claim)))
            if key in seen_entities:
                row = seen_entities[key]
                row['body_location'] += '\n' + unit['body_location']
                row['occurrences'].append({'claim': claim, 'body_location': unit['body_location'], 'refs': refs})
                row['refs'] = list(dict.fromkeys(row['refs'] + refs))
                row['source'] = '\n'.join(dict.fromkeys(row['source'].splitlines() + list(locators.values())))
                row['evidence'] += [e for e in excerpts(claim, locators, name) if e not in row['evidence']]
                continue
            row = {**unit, **fact, 'id': f'E{len(seen_entities) + 1}', 'kind': 'entity',
                   'category': '对象属性事实' if fact['value'] else '正文实体关系', 'refs': refs,
                   'source': '\n'.join(dict.fromkeys(locators.values())),
                   'evidence': excerpts(claim, locators, name),
                   'citation_assignment': citation_assignment,
                   'occurrences': [{'claim': claim, 'body_location': unit['body_location'], 'refs': refs}],
                   'evidence_basis': 'explicit_citation' if locators else 'not_cited'}
            seen_entities[key] = row
            rows.append(row)
        # Recommendations/questions are auditable but are not factual citation samples.
        for url in dict.fromkeys([v for v in locators.values() if _public_url(v)] if statement_type not in NON_FACT_TYPES else []):
            key = (url, _key(_visible_text(claim)))
            if key in seen_sources:
                row = seen_sources[key]
                row['body_location'] += '\n' + unit['body_location']
                row['refs'] = list(dict.fromkeys(row['refs'] + [ref for ref in refs if locators.get(ref) == url]))
                row['occurrences'].append({'claim': claim, 'body_location': unit['body_location'], 'refs': refs})
                continue
            cited = {ref: value for ref, value in locators.items() if value == url}
            row = {**unit, 'id': f'C{len(seen_sources) + 1}', 'kind': 'source',
                   'name': url, 'category': '正文公开引用', 'source': url,
                   'refs': [ref for ref in refs if ref in cited],
                   'statement_type': statement_type, 'citation_assignment': citation_assignment,
                   'occurrences': [{'claim': claim, 'body_location': unit['body_location'], 'refs': refs}],
                   'review_note': '须逐项核对引用组是否支撑本句各事实；不得把能打开网页当作引用正确',
                   'evidence': excerpts(claim, cited), 'evidence_basis': 'explicit_citation'}
            seen_sources[key] = row
            rows.append(row)
    return rows


def cited_catalog(rows, catalog):
    """Keep auxiliary material separate and limit it to explicitly cited locators."""
    locators = {value for row in rows for value in row.get('source', '').splitlines() if value}
    sources = [source for source in catalog.get('sources', [])
               if (source.get('locator') or source.get('file_name')) in locators]
    return {'sources': sources,
            'fragments': [f for f in catalog.get('fragments', []) if f.get('source') in locators],
            'writing_fragments': {key: f for key, f in catalog.get('writing_fragments', {}).items()
                                  if f.get('source') in locators}}
