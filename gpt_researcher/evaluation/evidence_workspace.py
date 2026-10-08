"""Run-scoped evidence snapshots shared by writing and independent review."""
import hashlib
import json
from pathlib import Path
import re


def save_snapshot(directory, name, value):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name + '.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)
    return str(path)


def stage_catalog(catalog):
    from backend.reporting.source_identity import repair_mojibake
    fragments = []
    for source in catalog.get('sources', []):
        source['title'] = repair_mojibake(source.get('title', ''))
        locator = source.get('locator') or source.get('file_name', '')
        for page in source.get('pages', []):
            text = page.get('text', '')
            fixed = repair_mojibake(text)
            if fixed != text:
                page['original_encoded_text'] = text
                page['text'] = text = fixed
                page['encoding_repair'] = 'reversible_latin1_utf8_sequences'
            try:
                decoded = text.encode('latin1').decode('utf-8')
                if len(re.findall(r'[\u4e00-\u9fff]', decoded)) > len(re.findall(r'[\u4e00-\u9fff]', text)):
                    page['text'] = text = decoded
                    page['encoding_repair'] = 'reversible_latin1_to_utf8'
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            if source.get('evidence_eligible') is False:
                continue  # Keep archived text, but do not offer rejected pages as evidence.
            for offset in range(0, len(text), 1400):
                passage = text[offset:offset + 1800]
                identity = f'{locator}:{page.get("page")}:{offset}:{passage}'
                fragments.append({'fragment_id': hashlib.sha256(identity.encode()).hexdigest()[:20],
                                  'source': locator, 'page': page.get('page'), 'offset': offset,
                                  'text': passage})
    return {**catalog, 'fragments': fragments}


def evaluation_view(markdown, citation_map):
    """Use published sentences with typed, unique IDs for evidence resolution.

    Original IDs may already be numeric after editorial rewriting. Classify by
    the registered locator instead of relying on those IDs retaining a prefix.
    """
    text = re.split(r'^#{1,6}\s*参考文献\s*$', markdown, maxsplit=1, flags=re.M)[0]
    definitions = []
    replacements = {}
    preferred = {}
    for label, source in citation_map.items():
        prefix = 'URL' if re.match(r'https?://', source.get('url') or '') else '(?:原文|文献|来源)'
        preferred[label] = next((ref for ref in source.get('original_ids', [])
                                 if re.fullmatch(r'\[' + prefix + r'\d+\]', ref)), None)
    unique = {ref for ref in preferred.values() if ref and list(preferred.values()).count(ref) == 1}
    used = set(unique)
    for index, (label, source) in enumerate(citation_map.items(), 1):
        locator = source.get('url') or source.get('file_name') or source.get('description', '')
        public_url = re.search(r'https?://[^\s<>\[\]]+', source.get('url') or '')
        prefix = 'URL' if public_url else '原文'
        ref = preferred[label] if preferred[label] in unique else None
        if ref is None:
            number = index
            while f'[{prefix}{number}]' in used:
                number += 1
            ref = f'[{prefix}{number}]'
            used.add(ref)
        number = label.strip('[]')
        replacements[f'[{label}](#ref-{number})'] = ref
        replacements[label] = ref
        definitions.append(f'- {ref} {locator}')
    if replacements:
        # Replace in one pass so generated IDs cannot be rewritten by another entry.
        pattern = '|'.join(re.escape(key) for key in sorted(replacements, key=len, reverse=True))
        text = re.sub(r'(?<!\[)(?:' + pattern + r')(?!\])',
                      lambda match: replacements[match[0]], text)
    return text.rstrip() + '\n\n## 证据来源列表\n' + '\n'.join(definitions)
