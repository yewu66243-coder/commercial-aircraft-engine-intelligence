"""Offline regression replay using the original run's saved evidence."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gpt_researcher.evaluation.evidence_workspace import evaluation_view
from gpt_researcher.evaluation.entity_evaluator import evaluate_report_entities
from gpt_researcher.evaluation.evidence_samples import evaluate_local_references
from gpt_researcher.evaluation.independent_judge import build_samples
from gpt_researcher.evaluation.source_evaluator import _extract_url_reference_contexts
from backend.reporting.evaluation_report import assess_run

record = json.loads(next(Path('outputs').glob('*c5d9_指标测评.json')).read_text(encoding='utf-8'))['run_statistics']
workspace = Path(record['evidence_workspace'])
published = json.loads((workspace / 'final_report.json').read_text(encoding='utf-8'))['published_markdown']
catalog = json.loads((workspace / 'source_catalog.json').read_text(encoding='utf-8'))
view = evaluation_view(published, record['citation_map'])
entities = evaluate_report_entities(view, record['task'], source_catalog=catalog, prefer_body=True)
samples = build_samples(view, entities, catalog)
local = evaluate_local_references(view, catalog)
summary = {
    'run_id': record['run_id'], 'mode': 'offline_evidence_resolution_only_no_model_verdict',
    'entity_candidates': entities['extracted_count'],
    'entity_candidates_with_saved_evidence': sum(s['kind'] == 'entity' and bool(s['evidence']) for s in samples),
    'public_url_references': len(_extract_url_reference_contexts(view)),
    'public_claim_samples': sum(s['kind'] == 'source' for s in samples),
    'public_claim_samples_with_saved_evidence': sum(s['kind'] == 'source' and bool(s['evidence']) for s in samples),
    'local_references': local['cited_count'], 'readable_local_references': local['readable_count'],
    'legacy_all_unresolved_display': assess_run(record)['metrics'][1]['value'],
    'note': 'Evidence availability is not factual accuracy; no original run or verdict is overwritten.'
}
assert summary['public_url_references'] == 4, summary
assert summary['entity_candidates_with_saved_evidence'] > 0, summary
assert summary['public_claim_samples_with_saved_evidence'] > 0, summary
Path('tmp/c5d9_citation_validation.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
