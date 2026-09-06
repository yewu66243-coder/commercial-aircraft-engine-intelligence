"""Convert internal evidence identifiers to a reversible public bibliography."""
from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
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
            if item['title']:
                author = item['author'].strip(' ;-')
                title = item['title'].removesuffix('_' + author) if author else item['title']
                description = ((author + '．') if author else '') + title.rstrip('。．') + '．'
                link = item['url'] or item['local_url']
                if link:
                    description += f' [查看原始资料]({link})'
            else:
                description = item['description']
                if item['url'] and description == item['url']:
                    description = f'网页资料（{urlsplit(item["url"]).netloc}）．[原始网页]({item["url"]})'
            lines += [f'<a id="ref-{label[1:-1]}"></a>', f'{label} {description}', '']
        if not self.public:
            lines.append('本次材料未提供可对应的正文引文，参考资料需要补充核对。')
        return '\n'.join(lines)
