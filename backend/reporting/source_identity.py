"""Conservative local-source resolution independent of bibliography formatting."""
import hashlib
from pathlib import Path
import re
import unicodedata


def source_id(locator):
    return hashlib.sha256(str(locator).encode('utf-8')).hexdigest()[:24]


def repair_mojibake(text):
    def decode(match):
        try:
            return match[0].encode('latin1').decode('utf8')
        except UnicodeDecodeError:
            return match[0]
    fixed = re.sub(r'(?:[\u00c2-\u00df][\u0080-\u00bf]|[\u00e0-\u00ef][\u0080-\u00bf]{2}|[\u00f0-\u00f4][\u0080-\u00bf]{3})+', decode, text)
    return fixed if len(re.findall(r'[\u4e00-\u9fff]', fixed)) > len(re.findall(r'[\u4e00-\u9fff]', text)) else text


def normalized_title(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', unicodedata.normalize('NFKC', str(text)).lower()).replace('_', '')


def resolve_local_source(description, sources):
    local = [s for s in sources if s.get('kind') != 'web' and s.get('file_name')]
    # Exact filenames are authoritative; a.pdf must not match data.pdf.
    exact = [s for s in local if re.search(
        r'(?<![\w])' + re.escape(s['file_name']) + r'(?![\w])', description)]
    if exact:
        return exact[0] if len(exact) == 1 else None
    # A conflicting explicit filename must not be rebound merely by similar title.
    if re.search(r'\.(?:pdf|docx?|txt|md|xlsx?)\b', description, re.I):
        return None
    # Match a complete bibliographic title, not an arbitrary substring of prose.
    prefix = re.split(r'\[(?:J|R|M|D|C|EB/OL)\]', description, maxsplit=1, flags=re.I)[0]
    prefix = prefix.rstrip(' .。')
    title_keys = {normalized_title(prefix)}
    title_keys.update(normalized_title(prefix[match.end():]) for match in re.finditer(r'[.。]\s*', prefix))
    matches = []
    for source in local:
        titles = {source.get('title', ''), Path(source['file_name']).stem.split('_')[0]}
        if any(len(normalized_title(title)) >= 8 and normalized_title(title) in title_keys
               for title in titles if title):
            matches.append(source)
    return matches[0] if len(matches) == 1 else None
