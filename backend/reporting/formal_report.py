"""Assemble readable research reports without adding facts or erasing caveats."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from .citations import CitationRegistry


@dataclass
class PreparedReport:
    markdown: str
    citation_map: dict
    quality: dict
    verification_notes: list = field(default_factory=list)


_VERIFICATION_ACTION = r'(?:有待|待)\s*(?:后续|进一步)?\s*(?:核验|核实|核查)'
_VERIFICATION_SUBJECT = r'(?:该点|此点|该项|此项)\s*'
_VERIFICATION_MARKER = re.compile(
    r'[（(]\s*(?:' + _VERIFICATION_SUBJECT + r')?' + _VERIFICATION_ACTION + r'\s*[。；;]?\s*[）)]'
    r'|' + _VERIFICATION_SUBJECT + _VERIFICATION_ACTION
)


def _move_verification_markers(text, section, notes):
    """Move editorial status labels to audit data, retaining substantive caveats."""
    def record(match):
        notes.append({'kind': 'inline_verification_marker', 'section': section,
                      'marker': match[0],
                      'context': text[max(0, match.start() - 180):match.end() + 100]})
        return ''

    cleaned, count = _VERIFICATION_MARKER.subn(record, text)
    if count:
        cleaned = re.sub(r'[，,；;]\s*([。！？）)])', r'\1', cleaned)
        cleaned = re.sub(r'[ \t]+([。！？；，、）])', r'\1', cleaned)
    return cleaned


def concise_title(text, task=''):
    title = re.sub(r'^#+\s*', '', text or task).strip().strip('*')
    title = re.split(r'[，,。；;]\s*(?:重点|主要|关注|请|要求|包含|涵盖)', title, maxsplit=1)[0]
    title = re.sub(r'\s+', ' ', title).rstrip('。；; ')
    if len(title) > 58:
        title = re.split(r'[，,。；;：:]', title, maxsplit=1)[0]
    if len(title) > 58:
        title = re.split(r'[，,。；;：:]', task, maxsplit=1)[0][:50].rstrip() + '专题研究报告'
    return title or '专题研究报告'


def _heading(text):
    text = text.strip().strip('*')
    text = re.sub(r'^(?:第[一二三四五六七八九十\d]+[章节]\s*|[一二三四五六七八九十]+[、.．]\s*|\d+(?:\.\d+)*[、.．]?\s+)', '', text)
    text = re.sub(r'^分论点[一二三四五六七八九十\d]*\s*[:：]\s*', '', text)
    return re.sub(r'[（(](?:不)?参与[^）)]*评估[）)]', '', text).strip()


def _clean_body(text, title=''):
    lines = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if title and _heading(stripped) == _heading(title):
            continue
        if re.match(r'^(?:\*\*)?分论点[一二三四五六七八九十\d]+\s*[:：]', stripped):
            line = re.sub(r'^\s*(\*\*)?分论点[一二三四五六七八九十\d]+\s*[:：]\s*', r'\1', line)
        for label, replacement in [('证据事实', ''), ('推理过程', '据此分析，'), ('推理', '据此推测，'),
                                   ('对任务的影响', '就本研究而言，'), ('对任务影响', '就本研究而言，'), ('结论', '')]:
            line = re.sub(r'(?:\*\*)?' + label + r'\s*[:：](?:\*\*)?\s*', replacement, line)
        if not re.match(r'^\s*#{1,6}\s', line):
            line = re.sub(r'(?<!\\)\*\*(?=\S)(.+?)(?<=\S)(?<!\\)\*\*', r'\1', line)
            line = re.sub(r'(?<![\w/\\])__(?=\S)(.+?)(?<=\S)__(?![\w/\\])', r'\1', line)
            line = re.sub(r'</?(?:strong|b)\b[^>]*>', '', line, flags=re.I)
        lines.append(line)
    return '\n'.join(lines).strip()


def _sections(markdown):
    title = ''
    sections = []
    name, body = '', []
    for line in markdown.splitlines():
        if re.match(r'^#\s+', line) and not title:
            title = line[2:].strip()
            continue
        if re.match(r'^##\s+', line):
            if name or any(body):
                sections.append((name, '\n'.join(body).strip()))
            name, body = _heading(line[3:]), []
        else:
            body.append(line)
    if name or any(body):
        sections.append((name, '\n'.join(body).strip()))
    return title, sections


def _number_subheadings(text, chapter):
    counters = [0, 0, 0, 0]
    lines = []
    for line in text.splitlines():
        match = re.match(r'^(#{3,6})\s+(.+)', line)
        if match:
            depth = min(len(match[1]) - 3, 3)
            for level in range(depth):
                counters[level] = max(1, counters[level])
            counters[depth] += 1
            counters[depth+1:] = [0] * (3-depth)
            numbers = '.'.join([str(chapter)] + [str(v) for v in counters[:depth+1]])
            line = f'{match[1]} {numbers} {_heading(match[2])}'
        lines.append(line)
    return '\n'.join(lines)


def _captions(markdown):
    lines = markdown.splitlines()
    output, figures, tables = [], 0, 0
    current_title = '资料对照'
    for index, line in enumerate(lines):
        if re.match(r'^#{2,6}\s', line):
            current_title = _heading(re.sub(r'^#+\s*', '', line))
        match = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)\s*$', line.strip())
        if match:
            figures += 1
            caption = re.sub(r'^图\s*\d+[.．、：:\s]*', '', match[1]).strip() or '资料图'
            output += [f'![图 {figures} {caption}]({match[2]})', '', f'图 {figures} {caption}', '']
            continue
        if line.lstrip().startswith('|') and index + 1 < len(lines) and re.match(r'^\s*\|?\s*:?-{3,}', lines[index+1]):
            tables += 1
            previous = next((item for item in reversed(output) if item.strip()), '')
            if not re.match(r'^\*{0,2}表\s*\d+', previous):
                output += [f'**表 {tables} {current_title}**', '']
        output.append(line)
    return '\n'.join(output), figures, tables


def prepare_formal_report(markdown, task, sources=(), metadata=None):
    metadata = metadata or {}
    registry = CitationRegistry(markdown, sources)
    raw_title, sections = _sections(markdown)
    warnings = []
    verification_notes = []
    front = {'abstract': '', 'keywords': '', 'intro': '', 'method': '', 'discussion': '', 'conclusion': ''}
    thematic, appendices = [], []
    for title, body in sections:
        keyword = re.search(r'^\s*\*{0,2}关键词\s*[:：]\s*\*{0,2}(.+)$', body, re.M)
        if keyword:
            front['keywords'] = keyword[1].strip().strip('*')
            body = body[:keyword.start()] + body[keyword.end():]
        if re.search(r'本次运行|运行统计|本次精读文献|证据来源|参考文献|^References$|^目录$|事实卡片摘要|内部核验|实体.*清单', title, re.I):
            if '待核验事项' in title and body.strip():
                verification_notes.append({'kind': 'internal_verification_section', 'section': title, 'context': body.strip()})
            continue
        body = _clean_body(_move_verification_markers(body, title, verification_notes), title)
        if not body:
            continue
        if title in {'摘要', '执行摘要', '中文摘要'}:
            front['abstract'] += body + '\n\n'
        elif title == '关键词':
            front['keywords'] = body
        elif re.match(r'引言|研究背景|前言', title) or not title:
            front['intro'] += body + '\n\n'
        elif re.search(r'资料来源与研究方法|数据来源与研究方法|^研究方法$', title):
            front['method'] += body + '\n\n'
        elif re.search(r'讨论|证据不足|核验建议|研究局限', title):
            front['discussion'] += body + '\n\n'
        elif re.search(r'结论|总结与建议', title):
            front['conclusion'] += body + '\n\n'
        elif title.startswith('附录'):
            appendices.append((title, body))
        else:
            thematic.append((title, body))

    if not front['intro']:
        front['intro'] = f'本报告围绕“{concise_title(task)}”组织已取得的资料，分析相关问题及证据的适用范围。'
        warnings.append('原稿缺少独立引言，已补入研究范围说明。')
    if not front['method']:
        scope = metadata.get('search_scope')
        if scope:
            front['method'] = f'资料检索范围为{scope}。'
            if metadata.get('retrieved_at'):
                front['method'] += f'本次检索记录时间为{str(metadata["retrieved_at"])[:10]}。'
            if sources:
                front['method'] += f'筛选后纳入本次分析的本地资料共{len(sources)}份。'
            front['method'] += '按研究主题归纳资料，并比较不同来源的表述、统计时间及适用范围。检索记录时间不等同于全部资料的发表截止时间。'
        else:
            front['method'] = '本报告依据已提供的资料进行主题归纳。原始检索条件与时间未完整记录，资料覆盖范围需要进一步核实。'
        warnings.append('原稿缺少研究方法章节，已据可用记录补入方法说明。')
    if not front['discussion']:
        front['discussion'] = '当前材料尚缺少独立的跨来源比较与局限讨论，需要在核对证据适用范围后补充。'
        warnings.append('综合讨论与研究局限需要补充。')
    if not front['conclusion']:
        front['conclusion'] = '当前材料尚未形成可核验的综合结论，需进一步完成证据分析。'
        warnings.append('原稿没有可用的结论章节。')
    if not front['abstract']:
        excerpt = re.sub(r'[#*]', '', front['conclusion']).strip()
        front['abstract'] = excerpt[:500] if len(excerpt) <= 500 else re.split(r'(?<=[。！？])', excerpt[:500])[:-1]
        if isinstance(front['abstract'], list):
            front['abstract'] = ''.join(front['abstract'])
        warnings.append('摘要由已有结论摘录，需要人工核对完整性。')
    if not front['keywords']:
        terms = re.findall(r'GTF(?:\s+Advantage)?|PW\d+[A-Z0-9-]*|LEAP(?:-[A-Z0-9]+)?|FADEC|维修保障|适航|粉末金属|涡轮冷却|航空发动机|供应链|专利', task + '\n' + markdown, re.I)
        terms = list(dict.fromkeys(terms))[:5]
        front['keywords'] = '；'.join(terms) or concise_title(task)
        warnings.append('关键词从原稿主题提取，需要人工确认。')

    title = concise_title(raw_title, task)
    draft = metadata.get('generation_status') == 'draft'
    if draft:
        title = title.removesuffix('（草稿）') + '（草稿）'
        warnings.append(metadata.get('generation_warning') or '综合写作未成功，当前为待整理草稿。')
    chapters = [('引言', front['intro']), ('资料来源与研究方法', front['method'])]
    if not thematic:
        warnings.append('缺少专题分析章节，需补充证据及分析。')
    chapters += thematic or [('专题资料分析', '当前尚无足够的专题分析内容。')]
    chapters += [('综合讨论与研究局限', front['discussion']), ('结论与建议', front['conclusion'])]
    parts = [f'# {title}', '', '## 摘要', '', front['abstract'].strip(), '', f'**关键词：** {front["keywords"]}', '']
    if metadata.get('include_toc') or len(markdown) > 6000:
        parts += ['## 目录', ''] + [f'- [{i} {name}](#sec-{i})' for i, (name, _) in enumerate(chapters, 1)]
        parts += ['- [参考文献](#references)', '']
    for index, (name, body) in enumerate(chapters, 1):
        parts += [f'<a id="sec-{index}"></a>', f'## {index} {name}', '', _number_subheadings(body.strip(), index), '']
    for index, (name, body) in enumerate(appendices, 1):
        name = re.sub(r'^附录\s*[A-Z一二三四五六七八九十\d]*[：:\s]*', '', name)
        parts += [f'## 附录 {chr(64 + index)} {name}', '', _number_subheadings(body, chr(64 + index)), '']
    body = registry.convert('\n'.join(parts))
    body, figure_count, table_count = _captions(body)
    # References precede appendices in the final publication, while numbering follows first appearance.
    appendix_match = re.search(r'^## 附录 ', body, re.M)
    bibliography = '<a id="references"></a>\n' + registry.bibliography()
    if appendix_match:
        body = body[:appendix_match.start()].rstrip() + '\n\n' + bibliography + '\n\n' + body[appendix_match.start():]
    else:
        body = body.rstrip() + '\n\n' + bibliography
    if registry.missing:
        warnings.append('部分正文引文没有对应的来源信息，须补充核对。')
    if not registry.public:
        warnings.append('正文未建立引用与参考文献的对应关系。')
    abstract_length = len(re.sub(r'\[[^\]]*\]|\s+', '', front['abstract']))
    if not 300 <= abstract_length <= 500:
        warnings.append('摘要篇幅需复核，建议正式报告采用300–500字。')
    keyword_count = len([v for v in re.split(r'[；;,，、]', front['keywords']) if v.strip()])
    if not 3 <= keyword_count <= 5:
        warnings.append('关键词数量需复核，建议3–5个。')
    quality = {
        'status': 'draft' if draft else ('needs_review' if warnings else 'ready'),
        'warnings': list(dict.fromkeys(warnings)), 'missing_source_ids': list(dict.fromkeys(registry.missing)),
        'reference_count': len(registry.public), 'figure_count': figure_count, 'table_count': table_count,
        'abstract_characters': abstract_length, 'keyword_count': keyword_count,
        'format_version': 'academic-report-v1',
    }
    return PreparedReport(body.strip() + '\n', registry.public, quality, verification_notes)
