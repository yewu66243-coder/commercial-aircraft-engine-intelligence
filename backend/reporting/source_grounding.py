"""Original-source context for writing and editorial review; drafts are not evidence."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re

from .citations import source_dict


def build_source_catalog(sources, sections, *, allow_web=False):
    records, errors = [], []
    for source in sources:
        item = source_dict(source)
        name = str(item.get('file_name') or '')
        path = Path(item.get('source_path') or '')
        pages = []
        try:
            if path.suffix.lower() == '.pdf':
                import fitz
                with fitz.open(path) as doc:
                    for index, page in enumerate(doc):
                        if index >= 80:
                            break
                        # PDF reading order preserves columns; coordinate sorting
                        # interleaves unrelated columns into otherwise literal quotes.
                        text = page.get_text('text', sort=False).strip()
                        if text:
                            pages.append({'page':index + 1, 'text':text[:18000]})
            elif path.suffix.lower() == '.docx':
                from docx import Document
                text = '\n'.join(p.text for p in Document(path).paragraphs)
                pages.append({'page':None, 'text':text[:90000]})
            elif path.suffix.lower() in {'.txt', '.md'}:
                pages.append({'page':None, 'text':path.read_text(encoding='utf-8')[:90000]})
            if not any(p['text'].strip() for p in pages):
                errors.append({'locator':name, 'reason':'没有读取到可核对的原文文本'})
        except Exception as exc:
            errors.append({'locator':name, 'reason':type(exc).__name__})
        records.append({'locator':name, 'file_name':name, 'kind':'local',
                        'title':item.get('title') or name, 'pages':pages})

    web = {}
    if allow_web:
        for section in sections:
            candidates = section.get('sources') or []
            if not isinstance(candidates, list):
                candidates = [candidates]
            else:
                candidates = list(candidates)
            contexts = section.get('context') or []
            if isinstance(contexts, list):
                candidates += [c for c in contexts if isinstance(c, dict)]
            for item in candidates:
                if isinstance(item, str):
                    if not re.fullmatch(r'https?://\S+', item.strip()):
                        continue
                    item = {'url':item.strip()}
                if not isinstance(item, dict):
                    continue
                metadata = item.get('metadata') or {}
                url = item.get('url') or item.get('source') or metadata.get('source') or ''
                if not re.fullmatch(r'https?://\S+', str(url)):
                    continue
                text = item.get('raw_content') or item.get('content') or item.get('page_content') or item.get('text') or ''
                if url not in web or len(str(text)) > len(web[url]['text']):
                    web[url] = {'locator':url, 'kind':'web', 'title':item.get('title') or url, 'text':str(text)}
        # Only URLs returned by this research run may be fetched. Never mine generated prose.
        missing = [value for value in list(web.values())[:20] if len(value['text']) < 120]
        if missing:
            from gpt_researcher.evaluation.entity_evaluator import _read_url_text
            def retrieve(item):
                try:
                    item['text'] = _read_url_text(item['locator'], limit=40000)
                except Exception:
                    item['text'] = ''
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(retrieve, missing))
        for item in list(web.values())[:20]:
            text = item.pop('text')
            item['pages'] = [{'page':None, 'text':text[:40000]}] if text else []
            if not text:
                errors.append({'locator':item['locator'], 'reason':'网页原文未取得'})
            records.append(item)
    return {'sources':records, 'errors':errors,
            'readable_sources':sum(bool(s['pages']) for s in records),
            'basis':'selected_local_files_and_retrieved_web_content'}


def source_text(source):
    return '\n'.join(p['text'] for p in source.get('pages', []))


def pack_sources(catalog, query, budget=60000):
    readable = [s for s in catalog['sources'] if s.get('pages')]
    if not readable or budget <= 0:
        return ''
    tokens = set(re.findall(r'[A-Za-z][A-Za-z0-9-]{2,}|[\u4e00-\u9fff]{2,6}', query.lower()))
    quota = budget // len(readable)
    blocks = []
    for source in readable:
        header = f'来源定位：{source["locator"]}\n题名：{source.get("title", "")}\n'
        if len(header) >= quota - 80:
            continue  # Never truncate a locator or emit an empty evidence entry.
        room = quota - len(header) - 4
        pages = source['pages']
        # Keep source units and page numbers when selecting excerpts from a long file.
        ranked = sorted(pages, key=lambda p:sum(p['text'].lower().count(t) for t in tokens), reverse=True)
        selected = []
        for page in ranked:
            label = f'第{page["page"]}页' if page.get('page') else '原文摘录'
            text = f'[{label}]\n{page["text"]}\n'
            if len(text) > room:
                if room > 200:
                    text = text[:room - 8] + '\n[摘录截断]\n'
                else:
                    break
            selected.append((page.get('page') or 0, text))
            room -= len(text)
            if room <= 0:
                break
        blocks.append(header + ''.join(text for _, text in sorted(selected)))
    return '\n\n'.join(blocks)[:budget]
