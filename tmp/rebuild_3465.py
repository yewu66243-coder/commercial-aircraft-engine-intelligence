import asyncio
import copy
from datetime import datetime
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from backend.reporting.formal_report import prepare_formal_report
from backend.reporting.evaluation_report import assess_run, render_evaluation_report
from gpt_researcher.evaluation.evidence_workspace import stage_catalog, evaluation_view, save_snapshot
from gpt_researcher.evaluation.entity_evaluator import evaluate_report_entities
from gpt_researcher.evaluation.evidence_samples import evaluate_local_references
from gpt_researcher.evaluation.source_evaluator import evaluate_public_url_sources
from gpt_researcher.evaluation.independent_judge import judge_report

async def main():
    load_dotenv(override=False)
    original = next(Path('outputs').glob('*3465_指标测评.json'))
    record = copy.deepcopy(json.loads(original.read_text(encoding='utf8'))['run_statistics'])
    started = time.perf_counter()
    record['original_run_id'] = record['run_id']
    record['run_id'] += '-recheck'
    record['evidence_workspace'] = str(Path('outputs/records/evidence') / record['run_id'])
    catalog = stage_catalog(record['original_source_catalog'])
    index = {s['file_name']: s for s in json.loads(Path('local_docs/papers_index.json').read_text(encoding='utf8')) if s.get('file_name')}
    sources = [dict(s, pages='', **{k:v for k,v in index.get(s['file_name'], {}).items() if k not in {'pages','locator'}},
                    source_path=str(Path('local_docs/all_papers_pool') / s['file_name']))
               for s in catalog['sources'] if s.get('file_name')]
    prepared = prepare_formal_report(record['evidence_report'], record['task'], sources,
        metadata={'source_catalog': catalog, 'generation_status': 'needs_review', 'include_toc': True})
    assert not prepared.quality['missing_source_ids'], prepared.quality['missing_source_ids']
    view = evaluation_view(prepared.markdown, prepared.citation_map)
    record['public_url_source_eval'] = evaluate_public_url_sources(view, source_catalog=catalog)
    record['entity_eval'] = evaluate_report_entities(view, record['task'], source_catalog=catalog, prefer_body=True)
    record['local_reference_eval'] = evaluate_local_references(view, catalog)
    print('References:', len(prepared.citation_map), 'Entity candidates:', record['entity_eval']['extracted_count'], flush=True)
    import os
    env = dict(os.environ, JUDGE_TOTAL_TIMEOUT_SECONDS='600', JUDGE_CONCURRENCY='4')
    record['independent_judge'] = await judge_report(view, record['entity_eval'], catalog,
        writer_model=record['model_provider']['model'], env=env, workspace=record['evidence_workspace'],
        log=lambda msg: print(msg, flush=True))
    record['citation_map'] = prepared.citation_map
    record['report_quality'] = prepared.quality
    record['report_quality']['warnings'].append('本次只修复引用并重新核验，正文事实未按裁判结论重写，请结合测评中的错误和证据不足项审阅。')
    record['recheck'] = {'completed_at': datetime.now().astimezone().isoformat(),
        'duration_seconds': round(time.perf_counter()-started, 2), 'original_record': str(original),
        'scope': '引用修复及重新核验；保留原正文，不代表本轮重新检索或重新写作'}
    record['original_source_catalog'] = catalog
    record['evaluated_report'] = view
    # No fresh end-to-end time claim for an offline recheck of an earlier report.
    record['original_total_duration_seconds'] = record.pop('total_duration_seconds', None)
    record['stage_durations_seconds'] = {'引用修复及重新核验': record['recheck']['duration_seconds']}
    save_snapshot(record['evidence_workspace'], 'source_catalog', catalog)
    save_snapshot(record['evidence_workspace'], 'final_report', {'published_markdown': prepared.markdown, 'evaluation_text': view})
    stem = Path('outputs/普惠GTF技术风险与维修保障_3465_修订版')
    stem.with_suffix('.md').write_text(prepared.markdown, encoding='utf8')
    assessment = assess_run(record)
    measurement = render_evaluation_report(record, assessment)
    measurement = measurement.replace('## 1 指标与判定', '本附件为既有报告的引用修复与重新核验。正文未按裁判结论重写；原运行耗时不作为本次修订的端到端耗时。\n\n## 1 指标与判定')
    Path(str(stem)+'_指标测评.md').write_text(measurement, encoding='utf8')
    Path(str(stem)+'_指标测评.json').write_text(json.dumps({'assessment':assessment,'run_statistics':record}, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'assessment':assessment, 'judge':{k:record['independent_judge'].get(k) for k in ['status','entity','source','errors']}}, ensure_ascii=False), flush=True)

asyncio.run(main())
