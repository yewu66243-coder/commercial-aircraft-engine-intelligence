"""Deterministic candidate sampling and reference resolution against saved originals."""
import re
import unicodedata

REF = re.compile(r'\[(?:(?:原文|文献|来源|URL)\s*\d+|\d+)\]', re.I)
NAME = re.compile(r'(?<![A-Za-z0-9])(?:[A-Z][A-Z0-9]*(?:[-/][A-Za-z0-9]+)+|[A-Z]{2,}[0-9]+[A-Za-z0-9]*|FAA|EASA|CAAC|CMAS|TBC|GTF)(?![A-Za-z0-9])'
                  r'|普惠|罗罗|空客|波音|中国航发|热障涂层|粉末冶金|高温合金|增材制造|无损检测|高压涡轮盘|高压压气机|风扇叶片|燃烧室|齿轮箱|热管理系统')
QUANTITY = re.compile(r'(?<![A-Za-z0-9.])\d+(?:,\d{3})*(?:\.\d+)?(?:\s*[～~—–-]\s*\d+(?:\.\d+)?)?\s*'
                      r'(?:%|％|亿美元|万美元|亿元|万元|小时|分钟|循环|架|台|张|套|个|家|倍|年|月|℃|MPa|kN)')


def _key(value):
    return re.sub(r'\s+', '', value).lower()


def body_units(report):
    """Keep citations with their actual line; do not borrow them from other paragraphs."""
    skip = False
    for line in report.splitlines():
        if re.match(r'^#{1,6}\s', line):
            skip = bool(re.search(r'摘要|目录|参考文献|证据来源|内部核验|附录|References', line, re.I))
            continue
        if skip or not line.strip() or re.match(r'^\s*(?:!\[|图源|图片来源|关键词|\*\*关键词)', line):
            continue
        if re.match(r'^\s*[-*]?\s*' + REF.pattern + r'\s+\S', line):
            continue
        # A citation after terminal punctuation still belongs to the preceding sentence.
        for unit in re.findall(r'.+?(?:[。！？](?:\s*' + REF.pattern + r')*|$)', line):
            if unit.strip():
                yield unit.strip()


def extract_body_candidates(report):
    samples, seen = [], set()
    for unit in body_units(report):
        refs = list(dict.fromkeys(REF.findall(unit)))
        claim = REF.sub('', unit).strip(' |*')
        names = list(NAME.finditer(claim))
        # Dates remain in the entity's claim as attributes, never standalone entities.
        quantities = [m for m in QUANTITY.finditer(claim) if not re.search(r'(?:年|月)$', m[0])]
        for token in names + (quantities if names else []):
            name = token.group(0)
            identity = (name, claim)
            if identity in seen:
                continue
            seen.add(identity)
            samples.append({'name': name, 'value': claim, 'type': '正文候选',
                            'evidence': ''.join(refs), 'evidence_supported': bool(refs),
                            'review_status': 'unreviewed', 'extraction_method': 'body_rule_candidate'})
    return samples


def saved_pages(ref, evidence_map, catalog):
    description = next((value for key, value in evidence_map.items() if _key(key) == _key(ref)), '')
    matches = []
    for source in (catalog or {}).get('sources', []):
        locator = source.get('locator') or source.get('file_name') or ''
        if locator and locator == description:
            matches.append(source)
    if not matches and not re.search(r'https?://', description):
        from backend.reporting.source_identity import resolve_local_source
        resolved = resolve_local_source(description, (catalog or {}).get('sources', []))
        if resolved:
            matches = [resolved]
    if not matches and re.search(r'https?://', description):
        from .entity_evaluator import URL_RE
        urls = {match.rstrip('.') for match in URL_RE.findall(description)}
        matches = [s for s in (catalog or {}).get('sources', []) if s.get('locator') in urls]
    if len(matches) != 1:
        return [], '来源编号未对应唯一已保存原文' if not matches else '来源名称重复，需核对具体文件'
    pages = [p for p in matches[0].get('pages', []) if str(p.get('text', '')).strip()]
    return pages, '' if pages else '来源已关联，但未保存可读取正文'


def evaluate_local_references(report, catalog):
    from .entity_evaluator import _extract_evidence_map
    definitions = _extract_evidence_map(report)
    refs = list(dict.fromkeys(ref for unit in body_units(report) for ref in REF.findall(unit)
                              if not ref.upper().startswith('[URL')))
    results = []
    for ref in refs:
        pages, reason = saved_pages(ref, definitions, catalog)
        results.append({'ref': ref, 'source': definitions.get(ref, ''),
                        'status': 'readable' if pages else 'unresolved',
                        'pages': [p.get('page') for p in pages],
                        'reason': reason or '引用已关联本轮保存的原文，可供核验'})
    count = sum(item['status'] == 'readable' for item in results)
    return {'cited_count': len(refs), 'readable_count': count,
            'readable_rate': count / len(refs) if refs else None, 'results': results,
            'note': '本地引用可追溯率仅检查引用是否关联可读原文，不代表结论正确率或公开链接准确率。'}


def check_saved_entity(entity, pages):
    from .entity_evaluator import _check_entity_against_source
    def normalize(value):
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).lower()
    checks = []
    numbers = [normalize(m.group(0)) for m in QUANTITY.finditer(entity.get('value', ''))]
    for page in pages:
        # Bound co-occurrence to a passage, not arbitrary matches across a whole PDF.
        text = page.get('text', '')
        for start in range(0, len(text), 900):
            excerpt = text[start:start + 1400]
            status, reason, confidence = _check_entity_against_source(entity, excerpt)
            missing = [n for n in numbers if n not in normalize(excerpt)]
            if status == 'supported' and missing:
                status, reason = 'partially_supported', '原文片段未同时命中句中全部数值及单位：' + '、'.join(missing)
            checks.append({'status': status, 'reason': reason, 'confidence': confidence,
                           'page': page.get('page'), 'excerpt': excerpt})
    if not checks:
        return {'status': 'unchecked', 'reason': '未保存可用原文', 'confidence': 0}
    rank = {'supported': 3, 'partially_supported': 2, 'unsupported': 1, 'unchecked': 0}
    return max(checks, key=lambda c: (rank[c['status']], c['confidence']))
