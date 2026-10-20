"""Local maintenance CLI; does not start a server or call an LLM."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gpt_researcher.evaluation.records import EvaluationRecordStore
from gpt_researcher.evaluation.manual_review import (
    create_review_package, import_review, summarize_runs, ManualReviewError, export_round_summary,
)
from gpt_researcher.evaluation.evidence_workspace import save_snapshot


def main():
    parser = argparse.ArgumentParser(description='人工指标核验：导出核验表、导入人工结果、汇总至少10轮测试。')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='列出运行编号及人工核验状态')
    export = commands.add_parser('export', help='为已保存报告导出全新核验包')
    export.add_argument('run_id')
    ingest = commands.add_parser('import', help='导入填写后的Excel并生成测评Word')
    ingest.add_argument('run_id')
    ingest.add_argument('file')
    summary = commands.add_parser('summary', help='汇总指定运行，每轮必须达标')
    summary.add_argument('run_ids', nargs='+')
    args = parser.parse_args()
    store = EvaluationRecordStore(ROOT / 'outputs/records/evaluation_records.json')
    try:
        if args.command == 'list':
            result = [{'run_id': r['run_id'], 'task': r.get('task'), 'completed_at': r.get('completed_at'),
                       'manual_status': r.get('manual_acceptance', {}).get('overall', '待核验'),
                       'workbook': r.get('manual_review_package', {}).get('workbook', '')}
                      for r in store.list_runs()]
        elif args.command == 'summary':
            records = [store.get_run(run_id) for run_id in args.run_ids]
            if any(r is None for r in records):
                raise ManualReviewError('部分运行编号不存在。')
            result = summarize_runs(records, ROOT)
            from datetime import datetime
            import uuid
            folder = ROOT / 'outputs/manual_reviews/summaries' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
            save_snapshot(folder, 'summary', result)
            result['paths'] = export_round_summary(result, folder)
        else:
            record = store.get_run(args.run_id)
            if record is None:
                raise ManualReviewError('运行编号不存在，请先执行list。')
            if args.command == 'export':
                result = create_review_package(record, ROOT)
            else:
                path = Path(args.file)
                if path.suffix.lower() != '.xlsx':
                    raise ManualReviewError('仅接受.xlsx核验表。')
                from gpt_researcher.evaluation.manual_workbook import MAX_UPLOAD_BYTES
                with path.open('rb') as stream:
                    content = stream.read(MAX_UPLOAD_BYTES + 1)
                result = import_review(record, content, ROOT)
                store.append_manual_review(args.run_id, result)
                result = {k: v for k, v in result.items() if k != 'rows'}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ManualReviewError, OSError, ValueError) as exc:
        print(f'操作未完成：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
