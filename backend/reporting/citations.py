"""Convert internal evidence identifiers to a reversible public bibliography."""
from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from urllib.parse import quote, unquote, urlsplit


REF_RE = re.compile(r"\[(?:(?:原文|URL|文献|来源)\s*\d+|\d+)\]", re.I)
DEFINITION_RE = re.compile(r"^\s*(?:[-*]\s+)?(\[(?:(?:原文|URL|文献|来源)\s*\d+|\d+)\])\s+(.+)$", re.I)
URL_RE = re.compile(r"https?://[^\s<>\[\]，。；]+", re.I)


def source_dict(source):
    if isinstance(source, dict):
        return dict(source)
    return asdict(source) if is_dataclass(source) else dict(vars(source))


def _key(label):
    return re.sub(r"\s+", "", label).replace('url', 'URL')


def _clean_author(author: str) -> str:
    author = re.sub(r'\s+', ' ', str(author or '')).strip(' ;-，,。.')
    if not author or author.lower() == 'nan':
        return ''
    parts = [item.strip() for item in re.split(r'[;；、]', author) if item.strip()]
    return ','.join(parts) if parts else author.replace('，', ',')


def _clean_reference_text(text: str) -> str:
    text = re.sub(r'\s*\[查看原始资料\]\([^)]+\)', '', str(text or ''))
    text = re.sub(r'\s*\[本地资料\]\([^)]+\)', '', text)
    text = text.replace('nan．', '').replace('nan. ', '')
    text = re.sub(r'\s+', ' ', text).strip(' 。.')
    return text


def _extract_reference_date(text: str) -> tuple[str, str]:
    match = re.search(r'\b(?:19|20)\d{2}(?:-\d{1,2}(?:-\d{1,2})?)?\b', text or '')
    if not match:
        return '', text
    date = match[0]
    rest = (text[:match.start()] + text[match.end():]).strip(' 。.,，')
    return date, rest


def _split_reference_description(description: str, url: str) -> dict:
    body = _clean_reference_text(description)
    if url:
        body = body.replace(url, '').strip(' 。.')
    date, body_without_date = _extract_reference_date(body)
    first_split = re.match(r'^(.{1,80}?)[.．。]\s*(.+)$', body_without_date)
    if first_split:
        author = _clean_author(first_split[1])
        remainder = first_split[2].strip(' 。.')
        second_split = re.match(r'^(.+?)[.．。]\s*(.+)$', remainder)
        title = (second_split[1] if second_split else remainder).strip(' 。.')
        source = (second_split[2] if second_split else '').strip(' 。.')
    else:
        author = ''
        title = body_without_date or (f'网页资料（{urlsplit(url).netloc}）' if url else body)
        source = ''
    return {'author': author, 'title': title.strip(' 。.'), 'source': source.strip(' 。.'), 'date': date}


def _reference_marker(item: dict, description: str, has_url: bool) -> str:
    source_type = str(item.get('source_type') or item.get('type') or '')
    probe = f'{source_type} {description} {item.get("title", "")} {item.get("url", "")}'
    if not has_url and re.search(r'报告|资料|用户|report', source_type, re.I):
        return '[R]'
    if re.search(r'专利|patent', probe, re.I):
        return '[P]'
    if has_url and re.search(r'doi\.org|论文|期刊|journal|学报|计算机集成制造系统', probe, re.I):
        return '[J/OL]'
    if not has_url and re.search(r'论文|期刊|journal|学报', source_type, re.I):
        return '[J]'
    if re.search(r'学位|dissertation|thesis', probe, re.I):
        return '[D]'
    return '[EB/OL]' if has_url else '[R]'


def _format_gbt_reference(item: dict) -> str:
    url = item.get('url') or ''
    local_url = item.get('local_url') or ''
    link = url or local_url
    parsed = _split_reference_description(item.get('description', ''), url)
    author = _clean_author(item.get('author') or parsed['author'])
    title = _clean_reference_text(item.get('title') or parsed['title'])
    marker = _reference_marker(item, item.get('description', ''), bool(url))
    date = parsed['date']
    source = _clean_reference_text(parsed['source'])
    accessed = datetime.now().strftime('%Y-%m-%d')

    if not title:
        title = f'网页资料（{urlsplit(url).netloc}）' if url else '来源信息缺失，待补充'
    prefix = f'{author}.' if author else ''

    if marker == '[P]':
        body = f'{prefix}{title}{marker}.'
        if date:
            body = f'{prefix}{title}{marker}.{date}.'
    elif marker in {'[J]', '[J/OL]'}:
        body = f'{prefix}{title}{marker}.'
        if source:
            body += f'{source}'
            if date:
                body += f',{date}'
            body += '.'
        elif date:
            body += f'{date}.'
    elif marker == '[EB/OL]':
        body = f'{prefix}{title}{marker}.'
        if date:
            body += f'{date}'
        body += f'[{accessed}].'
    else:
        body = f'{prefix}{title}{marker}.本地资料.'

    if marker in {'[J/OL]', '[EB/OL]'} and url:
        if f'[{accessed}]' not in body:
            body = body.rstrip('.') + f'[{accessed}].'
        body += f' [{url}]({url}).'
    elif local_url and marker not in {'[R]', '[J]', '[D]', '[P]'}:
        body = body.rstrip('.') + f'. [本地资料]({local_url}).'
    elif url and marker != '[R]':
        body = body.rstrip('.') + f'. {url}.'
    return re.sub(r'\s+', ' ', body).replace('. ', '.').strip()


class CitationRegistry:
    def __init__(self, markdown, sources=()):
        self.sources = [source_dict(item) for item in sources]
        self.definitions = {}
        self.public = {}
        self.identities = {}
        self.missing = []
        in_references = False
        for line in markdown.splitlines():
            if re.match(r'^#{1,6}\s', line):
                in_references = bool(re.search(r'参考文献|证据来源|References|资料来源列表', line, re.I))
            match = DEFINITION_RE.match(line)
            if match and in_references:
                self.definitions[_key(match[1])] = self._record(match[2])

    def _record(self, description, local_name=''):
        match = URL_RE.search(description)
        url = match[0].rstrip(').；;') if match else ''
        file_match = re.match(r'\s*[`\[\"\']?([^\n|]+?\.(?:pdf|docx?|txt|md|xlsx?))(?=[`\]\"\'（(\s|]|$)', description, re.I)
        file_name = unquote(local_name or (file_match[1].strip() if file_match else ''))
        # A filename must match in full; data.pdf must never bind to a.pdf.
        source = next((item for item in self.sources
                       if file_name and str(item.get('file_name') or '') == file_name), None)
        if source:
            source_type = str(source.get('source_type') or source.get('type') or '')
            target = 'patents' if '专利' in source_type else ('user_docs' if '用户' in source_type else 'papers')
            local_url = f'/api/local-library/{target}/{quote(file_name, safe="")}/open'
        else:
            local_url = ''
        return {
            'description': description.strip(), 'url': url, 'file_name': file_name,
            'title': str((source or {}).get('title') or ''),
            'author': str((source or {}).get('author') or ''),
            'source_type': str((source or {}).get('source_type') or ''),
            'local_url': local_url, 'resolved': bool(url or source),
        }

    def _resolve(self, label):
        key = _key(label)
        if key in self.definitions:
            record = self.definitions[key]
            if (key.upper().startswith('[URL') and not record['url']) or (
                key.startswith('[原文') and not record['local_url']
            ):
                self.missing.append(label)
                record = {**record, 'resolved': False, 'description': record['description'] + '（来源定位信息缺失，待核验）'}
            return record
        # Explicit local file / URL evidence is accepted as a locator, never as proof of validity.
        local = re.match(r'\[原文\s*[:：]\s*(.+)\]', label)
        remote = re.match(r'\[来源URL\s*[:：]\s*(https?://.+)\]', label, re.I)
        if local:
            return self._record(local[1], local[1])
        if remote:
            return self._record(remote[1])
        self.missing.append(label)
        return {'description': '来源信息缺失，待补充。', 'url': '', 'file_name': '',
                'title': '', 'author': '', 'local_url': '', 'source_type': '', 'resolved': False}

    def _cite(self, label):
        record = self._resolve(label)
        if not record['resolved']:
            self.missing.append(label)
            if '待' not in record['description']:
                record = {**record, 'description': record['description'] + '（来源尚未定位，待核验）'}
        identity = ('url', record['url']) if record['url'] else (
            ('file', record['file_name']) if record['file_name'] else ('label', _key(label)))
        public_id = self.identities.get(identity)
        if public_id is None:
            public_id = f'[{len(self.public) + 1}]'
            self.identities[identity] = public_id
            self.public[public_id] = {**record, 'original_ids': []}
        if label not in self.public[public_id]['original_ids']:
            self.public[public_id]['original_ids'].append(label)
        return public_id

    def convert(self, body):
        # A single pass is essential: syntax-specific passes reorder mixed citations.
        pattern = re.compile(
            r'\[\[(?P<public>\d+)\]\]\(#ref-\d+\)'
            r'|(?P<inline>\[(?:原文|来源URL)\s*[:：]\s*[^\]\n]+\])'
            r'|(?<!!)\[(?P<text>[^\]\n]+)\]\((?P<url>https?://[^\s)]+)\)'
            r'|(?P<short>\[(?:(?:原文|URL|文献|来源)\s*\d+|\d+)\])(?:\(#ref-\d+\))?', re.I)

        def replace(match):
            original = f'[{match["public"]}]' if match['public'] else (match['inline'] or match['short'] or f'[来源URL: {match["url"]}]')
            label = self._cite(original)
            linked_text = match['text'] or ''
            if re.fullmatch(r'(?:原文|URL|文献|来源)?\s*\d+', linked_text, re.I):
                linked_text = ''
            return f'{linked_text}[{label}](#ref-{label[1:-1]})'

        return pattern.sub(replace, body)

    def bibliography(self):
        lines = ['## 参考文献', '']
        for label, item in self.public.items():
            lines += [f'<a id="ref-{label[1:-1]}"></a>', f'{label}{_format_gbt_reference(item)}', '']
        if not self.public:
            lines.append('本次材料未提供可对应的正文引文，参考资料需要补充核对。')
        return '\n'.join(lines)
