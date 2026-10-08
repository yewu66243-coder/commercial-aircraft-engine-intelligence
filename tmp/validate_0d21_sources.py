import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gpt_researcher.evaluation.evidence_samples import evaluate_local_references, extract_body_candidates
from gpt_researcher.evaluation.independent_judge import build_samples
from backend.reporting.citations import CitationRegistry

record = json.loads(next(Path('outputs').glob('*0d21_指标测评.json')).read_text(encoding='utf-8'))['run_statistics']
workspace = Path(record['evidence_workspace'])
saved = json.loads((workspace / 'final_report.json').read_text(encoding='utf-8'))
catalog = json.loads((workspace / 'source_catalog.json').read_text(encoding='utf-8'))
report = saved['evaluation_text']
local = evaluate_local_references(report, catalog)
samples = build_samples(report, {'extracted_entities': extract_body_candidates(report)}, catalog)
registry = CitationRegistry('', catalog['sources'])
resolved = [registry._record(row['source']) for row in local['results']]
summary = {
    'run_id': record['run_id'], 'mode': 'offline_mapping_validation_no_model_judgement',
    'local_references': local['cited_count'], 'readable_local_references': local['readable_count'],
    'bibliographies_restored_to_files': sum(bool(s['file_name'] and s['resolved']) for s in resolved),
    'entity_candidates_with_evidence_before': sum(s['kind'] == 'entity' and bool(s['evidence']) for s in record['independent_judge']['results']),
    'entity_candidates_with_evidence_after': sum(s['kind'] == 'entity' and bool(s['evidence']) for s in samples),
    'entity_candidates_without_sentence_references': sum(s['kind'] == 'entity' and not s['refs'] for s in samples),
}
assert local['readable_count'] == local['cited_count'] == 5, summary
assert summary['bibliographies_restored_to_files'] == 5, summary
if '--sync-web' in sys.argv:
    from gpt_researcher.evaluation.source_evaluator import evaluate_public_url_sources
    result = evaluate_public_url_sources(report, source_catalog=catalog)
    samples = build_samples(report, {'extracted_entities': extract_body_candidates(report)}, catalog)
    summary['mode'] = 'saved_run_mapping_and_web_sync_validation_no_model_judgement'
    summary['web_references_with_readable_original'] = sum(row['source_readable'] for row in result['results'])
    summary['entity_candidates_with_evidence_after_web_sync'] = sum(s['kind'] == 'entity' and bool(s['evidence']) for s in samples)
    summary['public_claim_samples_with_evidence'] = sum(s['kind'] == 'source' and bool(s['evidence']) for s in samples)
    Path('tmp/0d21_synced_source_catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
Path('tmp/0d21_source_validation.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
