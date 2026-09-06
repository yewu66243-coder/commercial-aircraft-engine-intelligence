"""Conservative recovery of source figures omitted by the report writer."""
from pathlib import Path
import re

from .citations import source_dict


def normalize_figure_sources(report, images):
    """Bind each known figure's displayed source to its extraction metadata."""
    known = {i.get('markdown_path'):i for i in images if i.get('caption_matched')}
    pattern = r'!\[([^\]]*)\]\(([^)]+)\)(?:[ \t]*\r?\n(?:[ \t]*\r?\n)*[ \t]*\*{0,2}(?:图题|图源|图片来源)[:：][^\n]*)*'
    def replace(match):
        caption, path = match[1], match[2]
        candidate = known.get(path)
        if not candidate:
            return match[0]
        filename = re.sub(r'[\[\]\r\n*]', '', candidate['source_file'])
        notes = []
        def plain(value):
            return re.sub(r'[\s*。.]', '', value)
        for line in match[0].splitlines()[1:]:
            value = line.strip().strip('*')
            if value.startswith(('图题：','图题:')):
                extra = re.split(r'[:：]', value, maxsplit=1)[1].strip()
                if plain(extra) != plain(caption):
                    notes.append(extra)
            elif value.startswith(('图源：','图源:','图片来源：','图片来源:')):
                extra = re.split(r'第\s*\d+\s*页', value, maxsplit=1)
                if len(extra) == 2:
                    remainder = extra[1]
                else:
                    remainder = re.split(r'[:：]', value, maxsplit=1)[1]
                    remainder = re.sub(r'^.*?\.(?:pdf|docx?|txt|md)\b', '', remainder, count=1, flags=re.I)
                remaining = re.sub(r'\[(?:原文|URL|\d)[^\]]*\]', '', remainder).strip(' *。.,，；;')
                if remaining:
                    notes.append(remaining)
        annotation = ''.join('\n\n图注：' + note + f'[原文: {filename}]' for note in notes)
        return (f'![{caption}]({path})\n\n图源：{filename}，第 {candidate["page"]} 页。'
                f'[原文: {filename}]' + annotation)
    return re.sub(pattern, replace, report)


def _terms(text):
    text = text.casefold().replace('维护', '维修')
    latin = set(re.findall(r'[a-z][a-z0-9-]{2,}', text))
    text = re.sub(r'\s+', '', text)
    chinese = {block[i:i + 2] for block in re.findall(r'[\u4e00-\u9fff]+', text)
               for i in range(len(block) - 1)}
    return latin, chinese - {'发动', '动机', '航空', '公司', '研究', '分析', '相关', '技术', '问题', '资料'}


def _objects(text):
    """Separate product/model identifiers from ordinary English topic words."""
    words = re.findall(r'[A-Za-z][A-Za-z0-9-]{2,}', text)
    generic = {'mro', 'oem', 'faa', 'easa', 'caac', 'pdf', 'html', 'url', 'http', 'https'}
    return {w.casefold() for w in words
            if (w.isupper() or any(c.isdigit() for c in w) or w.casefold() in {'gtf', 'leap'})
            and w.casefold() not in generic}


def insert_missing_figures(report, images, query, sources, limit=2):
    """Insert only caption-matched local figures with a topic match, before references.

    This is a fallback when no figure was chosen. A match is a placement heuristic,
    not a factual validation of the source or an instruction to fill an image quota.
    """
    if re.search(r'!\[[^\]]*\]\([^)]+\)', report):
        return report, 0
    known = {source_dict(s).get('file_name') for s in sources}
    headings = list(re.finditer(r'^#{1,6}\s+([^\n]+)\n?', report, re.M))
    sections = []
    for i, heading in enumerate(headings):
        title = heading[1]
        if re.search(r'参考文献|证据来源|References|内部核验|待核验|附录', title, re.I):
            break
        if re.search(r'摘要|关键词|引言|资料来源|研究方法|结论|局限|综合讨论|目录', title):
            continue
        end = headings[i + 1].start() if i + 1 < len(headings) else len(report)
        if len(report[heading.end():end].strip()) >= 8:
            sections.append((title, end))
    q_latin, q_chinese = _terms(query)
    q_objects = _objects(query)
    insertions = []
    occupied = set()
    for candidate in images:
        if len(insertions) >= limit:
            break
        caption = candidate.get('caption', '')
        source = candidate.get('source_file', '')
        path = candidate.get('markdown_path', '')
        if (not candidate.get('caption_matched') or source not in known
                or not path.startswith('/outputs/report_images/')
                or not Path(candidate.get('image_path') or '').is_file()):
            continue
        c_latin, c_chinese = _terms(caption)
        # For model-specific requests, sharing only "maintenance" cannot bind a different engine.
        if q_objects and not _objects(caption).intersection(q_objects):
            continue
        if q_latin and not c_latin.intersection(q_latin):
            continue
        if not q_latin and len(c_chinese.intersection(q_chinese)) < 2:
            continue
        matches = []
        for title, end in sections:
            if end in occupied:
                continue
            h_latin, h_chinese = _terms(title)
            latin = c_latin & h_latin
            chinese = c_chinese & h_chinese
            if chinese or len(latin) >= 2:
                matches.append((len(chinese) + 2 * len(latin), end))
        if not matches:
            continue
        _, end = max(matches)
        caption = re.sub(r'[\[\]\r\n*]', '', caption).strip()
        source = re.sub(r'[\[\]\r\n*]', '', source).strip()
        block = (f'\n\n![{caption}]({path})\n\n'
                 f'图源：{source}，第 {candidate.get("page", "-")} 页。[原文: {source}]\n\n')
        insertions.append((end, block))
        occupied.add(end)
    for end, block in sorted(insertions, reverse=True):
        report = report[:end].rstrip() + block + report[end:]
    return report, len(insertions)
