"""Author-year syntax; resolution must use this run's registered sources."""
import re

AUTHOR_ITEM = r'[^\[\]\n;,，；]+[,，]\s*(?:\d{4}[a-z]?|n\.d\.)'
AUTHOR_GROUP = re.compile(r'\[(' + AUTHOR_ITEM + r'(?:\s*[;；]\s*' + AUTHOR_ITEM + r')*)\]', re.I)


def author_parts(group):
    return [re.split(r'[,，]\s*', item.strip(), maxsplit=1) for item in re.split(r'[;；]', group)]


def matching_labels(group, definitions, catalog):
    labels = []
    for author, year in author_parts(group):
        matches = []
        for label, record in definitions.items():
            locator = record.get('url') or record.get('file_name')
            original = next((s for s in catalog if s.get('locator') == locator or s.get('file_name') == locator), {}) if locator else {}
            pages = original.get('pages') or []
            head = '\n'.join(p.get('text', '') for p in pages[:1] if isinstance(p, dict))[:1200]
            metadata = ' '.join(str(record.get(k) or '') for k in ('author', 'title', 'file_name', 'description', 'year'))
            structured = metadata + ' ' + str(original.get('title', ''))
            metadata = structured + ' ' + head
            # Require the author/publisher name and date in recorded metadata or source header.
            author_match = re.search(r'(?<![A-Za-z])' + re.escape(author) + r'(?![A-Za-z])', metadata, re.I)
            if author_match and (year.lower() == 'n.d.' or year[:4] in metadata):
                score = 2 if re.search(r'(?<![A-Za-z])' + re.escape(author) + r'(?![A-Za-z])', structured, re.I) else 1
                matches.append((score, label))
        # Deduplicate aliases for the same physical source, never different works.
        identities = {}
        best = max((score for score, _ in matches), default=0)
        for score, label in matches:
            if score != best:
                continue
            record = definitions[label]
            identities.setdefault(record.get('url') or record.get('file_name') or label, label)
        if len(identities) != 1:
            return None
        labels.append(next(iter(identities.values())))
    return list(dict.fromkeys(labels))
