"""Offline evidence selection for human review; never calls a model."""
from .entity_evaluator import _extract_evidence_map, _important_terms, _term_hit_count
from .evidence_samples import REF, body_units, saved_pages
from .entity_retrieval import retrieve_entity_evidence


def _evidence(claim, refs, definitions, catalog, name='', expanded=False):
    terms = _important_terms(name + ' ' + claim, max_terms=40)
    name_terms = _important_terms(name, max_terms=12)
    excerpts = []
    for ref in refs:
        pages, _ = saved_pages(ref, definitions, catalog)
        locator = definitions.get(ref, ref)
        staged = list(catalog.get('writing_fragments', {}).values()) or catalog.get('fragments', [])
        pool = [f for f in staged if f.get('source') and (f['source'] == locator or f['source'] in locator)]
        if not pool or expanded:
            pool = []
            for page in pages:
                text = page['text']
                width, stride = (6000, 4800) if expanded else (1800, 1400)
                for start in range(0, len(text), stride):
                    pool.append({'text': text[start:start + width], 'page': page.get('page')})
        candidates = [(_term_hit_count(terms, f['text']) + 3 * _term_hit_count(name_terms, f['text']), f)
                      for f in pool]
        chosen = []
        for _, fragment in sorted(candidates, key=lambda c: c[0], reverse=True):
            if any(fragment['text'] in previous['text'] for previous in chosen):
                continue
            chosen.append(fragment)
            if len(chosen) >= (4 if expanded else 2):
                break
        for fragment in chosen:
            excerpts.append({'id': f'S{len(excerpts) + 1}', 'ref': ref,
                             'source': locator, 'page': fragment.get('page'), 'text': fragment['text'],
                             'fragment_id': fragment.get('fragment_id'),
                             'selection': 'expanded_original' if expanded else 'saved_fragment'})
    return excerpts


def build_samples(report, entity_eval, catalog):
    definitions = _extract_evidence_map(report)
    samples, cache = [], {}

    def evidence(claim, refs, name=''):
        key = (name, claim, tuple(refs))
        if key not in cache:
            cache[key] = _evidence(claim, refs, definitions, catalog, name=name)
        return cache[key]

    for index, entity in enumerate(entity_eval.get('extracted_entities') or []):
        claim = str(entity.get('value') or entity.get('name') or '')
        refs = REF.findall(str(entity.get('evidence') or ''))
        found = evidence(claim, refs, entity.get('name', ''))
        if not refs:
            found = retrieve_entity_evidence(entity.get('name', ''), claim, catalog)
        samples.append({'id': f'E{index + 1}', 'kind': 'entity', 'name': entity.get('name', ''),
                        'category': entity.get('type', '正文候选'),
                        'claim': claim, 'refs': refs, 'evidence': found,
                        'evidence_basis': 'explicit_citation' if refs else 'saved_source_retrieval'})
    seen = set()
    for unit in body_units(report):
        claim = REF.sub('', unit).strip()
        for ref in REF.findall(unit):
            if not ref.upper().startswith('[URL') or (ref, claim) in seen:
                continue
            seen.add((ref, claim))
            samples.append({'id': f'C{len(seen)}', 'kind': 'source', 'name': ref,
                            'category': '公开引用', 'claim': claim, 'refs': [ref],
                            'source': definitions.get(ref, ''), 'evidence': evidence(claim, [ref])})
    return samples
