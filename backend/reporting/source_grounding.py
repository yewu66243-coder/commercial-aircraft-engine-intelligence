"""Original-source context for writing and editorial review; drafts are not evidence."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import re

from .citations import source_dict
from .source_identity import source_id, repair_mojibake
from gpt_researcher.retrievers.web_evidence_policy import evidence_assessment, passage_score


def build_source_catalog(sources, sections, *, allow_web=False, query='', allow_fetch=True):
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
                from gpt_researcher.document.text_recovery import read_recovered_text
                recovered = read_recovered_text(path)
                if recovered:
                    pages = [{'page':p['page'], 'text':p['text'][:18000],
                              'extraction_method':p.get('method', recovered['method'])}
                             for p in recovered['pages'] if p['text'].strip()]
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
            candidates += list(section.get('web_evidence') or [])
            candidates += list(section.get('web_candidates') or [])
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
                record = web.setdefault(url, {'locator':url, 'kind':'web', 'title':url,
                                              'text':'', 'queries':[], 'searched':True})
                if len(str(text)) > len(record['text']):
                    record['text'] = str(text)
                for key in ('title', 'published_date'):
                    if item.get(key):
                        record[key] = item[key]
                priority = {'success':3, 'failed':2, 'not_attempted':1}
                if priority.get(item.get('fetch_status'), 0) > priority.get(record.get('fetch_status'), 0):
                    record['fetch_status'] = item['fetch_status']
                    record['fetch_reason'] = item.get('fetch_reason', '')
                if item.get('query') and item['query'] not in record['queries']:
                    record['queries'].append(item['query'])
        # Only URLs returned by this research run may be fetched. Never mine generated prose.
        # Compatibility fallback for legacy retrievers without a fetch ledger.
        # This is a fetch budget only; every URL and every saved body is retained.
        missing = [value for value in web.values() if not value['text'] and not value.get('fetch_status')]
        for value in missing:
            value['fetch_status'] = 'not_attempted'
            value['fetch_reason'] = '未抓取：兼容补抓预算以外' if allow_fetch else '离线复核，不补抓网页'
        missing = missing[:20] if allow_fetch else []
        if missing:
            from gpt_researcher.evaluation.entity_evaluator import _read_url_text
            def retrieve(item):
                try:
                    item['text'] = _read_url_text(item['locator'], limit=40000)
                except Exception:
                    item['text'] = ''
                item['fetch_status'] = 'success' if item['text'] else 'failed'
                item['fetch_reason'] = '' if item['text'] else '兼容补抓未取得正文'
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(retrieve, missing))
        for item in web.values():
            text = item.pop('text')
            item['pages'] = [{'page':None, 'text':text}] if text else []
            if text:
                item['fetch_status'] = 'success'
                item['fetch_reason'] = ''
            item['title'] = repair_mojibake(item['title'])
            item.update(evidence_assessment(query, item['title'], repair_mojibake(text), item['locator'], item.get('published_date')))
            if not text:
                errors.append({'locator':item['locator'], 'reason':'网页原文未取得'})
            records.append(item)
    for record in records:
        record['source_id'] = source_id(record['locator'])
    return {'sources':records, 'errors':errors,
            'query':query,
            'readable_sources':sum(bool(s['pages']) for s in records),
            'basis':'selected_local_files_and_retrieved_web_content'}


def source_text(source):
    return '\n'.join(p['text'] for p in source.get('pages', []))


def pack_sources(catalog, query, budget=60000, *, purpose='writing'):
    readable = [s for s in catalog['sources'] if s.get('pages') and s.get('evidence_eligible', True)]
    if purpose == 'writing':
        catalog['writing_fragments'] = {}
        for source in catalog['sources']:
            source['writing_selected'] = False
            source['selection_reason'] = source.get('exclusion_reason') or '未进入写作原文预算'
    if not readable or budget <= 0:
        return ''
    # Relevance first, then reward new topic/domain coverage. No input-order cap.
    ordered, covered, hosts = [], set(), set()
    from urllib.parse import urlsplit
    pending = list(readable)
    for source in pending:
        if 'relevance_score' not in source:
            assessment = evidence_assessment(query, source.get('title'), source_text(source))
            source['relevance_score'] = assessment['relevance_score']
            source['topics'] = assessment['topics']
    while pending:
        def rank(source):
            host = urlsplit(source['locator']).hostname or 'local'
            return (source.get('relevance_score', 0) + passage_score(source.get('title', ''), query)
                    + 4 * len(set(source.get('topics', [])) - covered) + (2 if host not in hosts else 0))
        chosen = max(pending, key=rank)
        pending.remove(chosen)
        ordered.append(chosen)
        covered.update(chosen.get('topics', []))
        hosts.add(urlsplit(chosen['locator']).hostname or 'local')
    blocks, used = [], 0
    for source in ordered:
        header = f'来源定位：{source["locator"]}\n题名：{source.get("title", "")}\n'
        # Up to two substantial passages per source; do not dilute into tiny quotas.
        room = min(4600, budget - used - len(header) - (2 if blocks else 0))
        if room < 120:
            continue
        passages = [(page, offset, page['text'][offset:offset+2200])
                    for page in source['pages'] for offset in range(0, len(page['text']), 2200)]
        ranked = sorted(passages, key=lambda row: passage_score(row[2], query), reverse=True)
        selected = []
        for page, offset, passage in ranked[:2]:
            label = f'第{page["page"]}页' if page.get('page') else '原文摘录'
            if page.get('extraction_method') == 'ocr':
                label += '，OCR识别文本'
            text = f'[{label}]\n{passage}\n'
            if len(text) > room:
                if room > 200:
                    text = text[:room - 8] + '\n[摘录截断]\n'
                else:
                    break
            selected.append((page.get('page') or 0, offset, text))
            passage = text.partition('\n')[2].split('\n[摘录截断]')[0].rstrip('\n')
            identity = f'{source["locator"]}:{page.get("page")}:{passage}'
            fragment_id = hashlib.sha256(identity.encode()).hexdigest()[:20]
            stored = catalog.setdefault(purpose + '_fragments', {})
            stored[fragment_id] = {'fragment_id': fragment_id, 'source': source['locator'],
                                   'page': page.get('page'), 'text': passage}
            room -= len(text)
            if room <= 0:
                break
        if selected:
            block = header + ''.join(text for _, _, text in sorted(selected))
            used += len(block) + (2 if blocks else 0)
            blocks.append(block)
            if purpose == 'writing':
                source['writing_selected'] = True
                source['selection_reason'] = '按相关性、已知发布日期和专题覆盖选入写作原文'
    return '\n\n'.join(blocks)
