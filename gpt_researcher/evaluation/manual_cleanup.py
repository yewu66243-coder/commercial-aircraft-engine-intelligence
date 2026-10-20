"""Conservative export cleanup, separate from evidence or accuracy judgments."""
from copy import deepcopy
import re
import unicodedata


_INVISIBLE = re.compile(r'[\s\u200b-\u200f\u2060\ufeff]+')
_UTF8_AS_LATIN1 = re.compile(
    r'(?:[\u00c2-\u00df][\u0080-\u00bf]|[\u00e0-\u00ef][\u0080-\u00bf]{2}'
    r'|[\u00f0-\u00f4][\u0080-\u00bf]{3})')
_MOJIBAKE_MARKERS = re.compile(r'锟斤拷|锛|銆|鈥|馃|鐨|鍙|闂|鍒|绔|鏂|鏈|娴|嬭|鎶')


def text_problem(value):
    """Flag only strong corruption signals; a missing excerpt is not a bad fact."""
    compact = _INVISIBLE.sub('', str(value or ''))
    if not compact:
        return 'empty'
    damaged = sum(c == '\ufffd' or unicodedata.category(c) in {'Co', 'Cs'}
                  or (unicodedata.category(c) == 'Cc' and not c.isspace()) for c in compact)
    if damaged >= 3 and damaged / len(compact) >= .03:
        return 'garbled'
    if not any(c.isalnum() for c in compact):
        return 'empty'
    if compact.count('锟斤拷') >= 2:
        return 'garbled'
    if len(_UTF8_AS_LATIN1.findall(compact)) >= 3:
        return 'garbled'
    # UTF-8 Chinese decoded as GBK often consists solely of valid CJK code points.
    if len(_MOJIBAKE_MARKERS.findall(compact)) >= 3:
        try:
            decoded = compact.encode('gb18030').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        else:
            if len(re.findall(r'[\u4e00-\u9fff]', decoded)) >= 3:
                return 'garbled'
    return ''


def clean_review_rows(rows, audit):
    """Omit invalid export rows without retaining their contents elsewhere."""
    kept = []
    audit = deepcopy(audit)
    summary = {'version': 'export-text-cleanup-v2', 'input_rows': len(rows),
               'removed_rows': 0, 'missing_source_rows': 0,
               'removed_excerpts': 0, 'missing_evidence_rows': 0}
    for original in rows:
        row = deepcopy(original)
        problems = {field: text_problem(row.get(field)) for field in ('claim', 'name')}
        problems = {field: problem for field, problem in problems.items() if problem}
        if not _INVISIBLE.sub('', str(row.get('source') or '')):
            problems['source'] = 'empty'
            summary['missing_source_rows'] += 1
        if problems:
            summary['removed_rows'] += 1
            continue
        evidence = []
        for excerpt in row.get('evidence') or []:
            problem = text_problem(excerpt.get('text'))
            if problem:
                summary['removed_excerpts'] += 1
            else:
                evidence.append(excerpt)
        row['evidence'] = evidence
        notes = []
        if len(evidence) != len(original.get('evidence') or []):
            notes.append('空白或明显乱码摘录已移除')
        if not evidence:
            summary['missing_evidence_rows'] += 1
            notes.append('无可读引用原文摘录，保留待核验，不自动判为正确或排除')
        if notes:
            row['review_note'] = '；'.join(filter(None, [row.get('review_note', ''), *notes]))
        kept.append(row)
    summary['output_rows'] = len(kept)
    return kept, audit, summary
