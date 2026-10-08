import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.reporting.formal_report import prepare_formal_report
from backend.reporting.evaluation_report import assess_run, render_evaluation_report
from gpt_researcher.evaluation.evidence_workspace import evaluation_view, save_snapshot
from gpt_researcher.evaluation.entity_evaluator import evaluate_report_entities

stem = Path('outputs/普惠GTF技术风险与维修保障_3465_修订版')
data_path = Path(str(stem) + '_指标测评.json')
record = json.loads(data_path.read_text(encoding='utf8'))['run_statistics']
catalog = record['original_source_catalog']
index = {s['file_name']: s for s in json.loads(Path('local_docs/papers_index.json').read_text(encoding='utf8')) if s.get('file_name')}
sources = [{**s, **index.get(s['file_name'], {}), 'pages': '',
            'source_path': str(Path('local_docs/all_papers_pool') / s['file_name'])}
           for s in catalog['sources'] if s.get('file_name')]
prepared = prepare_formal_report(record['evidence_report'], record['task'], sources,
    metadata={'source_catalog': catalog, 'generation_status': 'needs_review', 'include_toc': True})
assert not prepared.quality['missing_source_ids'], prepared.quality
identity = lambda entries: {k: (v.get('url'), v.get('file_name')) for k,v in entries.items()}
assert identity(prepared.citation_map) == identity(record['citation_map'])
view = evaluation_view(prepared.markdown, prepared.citation_map)
# Bibliographic metadata may change; evaluated body and source identities must not.
assert view.split('## 参考文献')[0] == record['evaluated_report'].split('## 参考文献')[0]
record['citation_map'] = prepared.citation_map
record['report_quality'] = prepared.quality
record['report_quality']['warnings'].append('本次只修复引用并重新核验，正文事实未按裁判结论重写，请结合测评中的错误和证据不足项审阅。')
record['evaluated_report'] = view
record['entity_eval'] = evaluate_report_entities(view, record['task'], source_catalog=catalog, prefer_body=True)
assert record['entity_eval']['extracted_count'] == record['independent_judge']['entity']['total']
save_snapshot(record['evidence_workspace'], 'final_report', {'published_markdown': prepared.markdown, 'evaluation_text': view})
stem.with_suffix('.md').write_text(prepared.markdown, encoding='utf8')
assessment = assess_run(record)
measurement = render_evaluation_report(record, assessment)
measurement = measurement.replace('## 1 指标与判定', '本附件为既有报告的引用修复与重新核验。正文未按裁判结论重写；原运行耗时不作为本次修订的端到端耗时。\n\n## 1 指标与判定')
Path(str(stem)+'_指标测评.md').write_text(measurement, encoding='utf8')
data_path.write_text(json.dumps({'assessment':assessment,'run_statistics':record}, ensure_ascii=False, indent=2), encoding='utf8')
print(prepared.markdown[prepared.markdown.rfind('## '):])
