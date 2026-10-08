"""Per-URL research disposition, separate from claim verification metrics."""
import re
from urllib.parse import urldefrag


def build_web_source_tracking(catalog, citation_map=None, report=''):
    body = re.split(r'^#{1,6}\s*参考文献\s*$', report, maxsplit=1, flags=re.M)[0]
    cited = set()
    for label, source in (citation_map or {}).items():
        url = source.get('url') or ''
        if url.startswith(('http://', 'https://')) and label in body:
            cited.add(urldefrag(url)[0].rstrip('/'))
    rows = []
    for source in catalog.get('sources', []):
        if source.get('kind') != 'web':
            continue
        url = source['locator']
        raw_length = sum(len(p.get('text', '')) for p in source.get('pages', []))
        selected = bool(source.get('writing_selected'))
        is_cited = urldefrag(url)[0].rstrip('/') in cited
        status = source.get('fetch_status') or ('success' if raw_length else 'unknown')
        eligible = bool(raw_length) and source.get('evidence_eligible', True)
        if is_cited:
            reason = '正文已引用；是否支撑结论请看指标核验结果'
            if not eligible:
                reason += '；注意：该来源未通过原文初筛'
        elif not raw_length:
            reason = source.get('fetch_reason') or '未取得网页原文'
        elif not eligible:
            reason = source.get('exclusion_reason') or '未通过原文初筛'
        elif not selected:
            reason = source.get('selection_reason') or '未进入写作原文预算'
        else:
            reason = '已送入写作原文上下文，最终正文未引用；未记录模型的具体取舍理由'
        rows.append({'url':url, 'title':source.get('title') or url,
                     'searched':bool(source.get('searched')), 'queries':source.get('queries', []),
                     'fetch_status':status, 'raw_characters':raw_length,
                     'evidence_eligible':eligible, 'writing_selected':selected, 'cited':is_cited,
                     'published_date':source.get('published_date') or '',
                     'recency':source.get('recency') or '发布日期未知',
                     'reason':reason, 'exclusion_reason':source.get('exclusion_reason') or ''})
    # A citation added during writing may not be part of search retrieval.
    recorded = {urldefrag(row['url'])[0].rstrip('/') for row in rows}
    for url in sorted(cited - recorded):
        rows.append({'url':url, 'title':url, 'searched':False, 'queries':[],
                     'fetch_status':'unknown', 'raw_characters':0, 'evidence_eligible':False,
                     'writing_selected':False, 'cited':True, 'published_date':'', 'recency':'发布日期未知',
                     'reason':'正文已引用，但未在本轮来源目录找到记录，需检查来源', 'exclusion_reason':''})
    summary = {'total_urls':len(rows),
               'searched':sum(r['searched'] for r in rows),
               'fetched':sum(r['fetch_status'] == 'success' for r in rows),
               'fetch_failed':sum(r['fetch_status'] == 'failed' for r in rows),
               'not_attempted':sum(r['fetch_status'] == 'not_attempted' for r in rows),
               'eligible':sum(r['evidence_eligible'] for r in rows),
               'excluded':sum(bool(r['raw_characters']) and not r['evidence_eligible'] for r in rows),
               'writing_selected':sum(r['writing_selected'] for r in rows),
               'cited':sum(r['cited'] for r in rows)}
    return {'summary':summary, 'sources':rows,
            'note':'按URL去重；取得文本不等于取得有效文章，选入上下文或被引用也不等于已核验正确。'}
