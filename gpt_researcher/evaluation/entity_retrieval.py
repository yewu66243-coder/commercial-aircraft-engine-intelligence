"""Retrieve candidate evidence for uncited entities; retrieval is not a verdict."""
import re
import unicodedata


def retrieve_entity_evidence(name, claim, catalog, limit=4):
    from .entity_evaluator import _important_terms, _term_hit_count
    def norm(text):
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).lower()
    needle = norm(name)
    if not needle:
        return []
    terms = _important_terms(claim, max_terms=40)
    candidates = []
    for source in (catalog or {}).get('sources', []):
        if source.get('evidence_eligible') is False:
            continue
        for page in source.get('pages', []):
            text = page.get('text', '')
            for start in range(0, len(text), 1000):
                passage = text[start:start + 1800]
                if needle not in norm(passage):
                    continue
                candidates.append((_term_hit_count(terms, passage), source, page, passage))
    evidence = []
    for score, source, page, passage in sorted(candidates, key=lambda row: row[0], reverse=True):
        if any(passage == e['text'] for e in evidence):
            continue
        evidence.append({'id': f'S{len(evidence) + 1}', 'ref': '',
                         'source': source['locator'], 'page': page.get('page'), 'text': passage,
                         'selection': 'uncited_entity_retrieval', 'retrieval_score': score})
        if len(evidence) >= limit:
            break
    return evidence
