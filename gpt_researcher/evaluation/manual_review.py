"""Version-bound, offline human acceptance review and reproducible scoring."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
from html import escape
import json
import math
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit, unquote

from .evidence_samples import REF
from .evidence_workspace import save_snapshot
from .manual_extraction import extract_published_rows, cited_catalog
from .manual_cleanup import clean_review_rows
from .manual_workbook import (ManualReviewError, write_workbook, read_workbook,
                              fixed_values, fact_values, audit_values, AUDIT_HEADERS,
                              HEADERS, V3_HEADERS, LEGACY_HEADERS, DECISIONS, MAX_ROWS)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_VERSION = 'human-acceptance-v3-object-attribute'


def _root(project_root):
    return Path(project_root or PROJECT_ROOT).resolve()


def _digest(value):
    return sha256(value).hexdigest()


def _now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _run_folder(run_id, root):
    return root / 'outputs' / 'manual_reviews' / _digest(str(run_id).encode())[:24]


def _output_path(value, root):
    raw = unquote(str(value or '')).replace('\\', '/')
    if raw.startswith('/outputs/'):
        raw = raw[1:]
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to((root / 'outputs').resolve()):
        raise ManualReviewError('报告文件不在项目outputs目录内。')
    return path


def report_identity(record, project_root=None):
    root = _root(project_root)
    artifacts = {}
    for key in ('md_path', 'word_path', 'pdf_path'):
        if record.get(key):
            path = _output_path(record[key], root)
            if not path.is_file():
                raise ManualReviewError('报告文件已移动或缺失，请恢复本次报告文件后再核验。')
            artifacts[key] = {'path': str(path.relative_to(root)), 'sha256': _digest(path.read_bytes())}
    if 'md_path' not in artifacts:
        raise ManualReviewError('缺少最终Markdown原件，不能用排版前正文替代核验。')
    report = (root / artifacts['md_path']['path']).read_text(encoding='utf-8-sig')
    if not report.strip():
        raise ManualReviewError('最终报告正文为空。')
    published = record.get('published_markdown')
    if published is not None and published.replace('\r\n', '\n') != report.replace('\r\n', '\n'):
        raise ManualReviewError('报告正文已修改，须登记新运行版本，不能沿用旧运行记录。')
    return {'report_sha256': _digest(report.encode('utf-8')), 'artifacts': artifacts}, report


def _public_link(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {'http', 'https'} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _materials_html(manifest, report, catalog, root):
    parts = ['<!doctype html><meta charset="utf-8"><title>人工核验材料</title>',
             '<style>body{font:16px/1.6 sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;color:#222}'
             'pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px solid #ccc;padding:16px 0}'
             'a{color:#086878}</style>', '<h1>人工核验材料</h1>',
             '<p>核验对象仅来自最终正文（含摘要、正文表格）。引用原文摘录仅辅助核对，不代表判定；无明确引用的实体不自动匹配来源。</p>',
             '<details><summary>最终报告原文</summary><pre>' + escape(report) + '</pre></details>']
    sources = catalog.get('sources', [])
    for row in manifest['rows']:
        parts.append(f'<section id="{row["id"]}"><h2>{escape(row["id"] + " " + row["name"])}</h2>')
        parts.append('<p>正文位置：' + escape(row.get('body_location', '')) + '</p>')
        parts.append('<p>' + escape('；'.join(str(row.get(key, '')) for key in
                     ('attribute', 'raw_value', 'conditions', 'statement_type', 'citation_assignment', 'review_note')
                     if row.get(key))) + '</p>')
        parts.append('<pre>' + escape(row['claim']) + '</pre>')
        if row.get('parent_claim'):
            parts.append('<details><summary>分句所在完整原句</summary><pre>' + escape(row['parent_claim']) + '</pre></details>')
        if row.get('source') and _public_link(row['source']):
            parts.append(f'<a href="{escape(row["source"], quote=True)}" rel="noreferrer">打开引用网页</a>')
        for e in row.get('evidence', []):
            locator = str(e.get('source', ''))
            parts.append('<h3>' + escape(locator + ' / 页码：' + str(e.get('page') or '未记录')) + '</h3>')
            parts.append('<pre>' + escape(str(e.get('text', ''))) + '</pre>')
            for index, source in enumerate(sources):
                if (source.get('locator') or source.get('file_name')) == locator:
                    parts.append(f'<a href="#source-{index}">查看此来源全部已保存原文</a>')
        parts.append('</section>')
    parts.append('<h2>提取审计（须逐段复核漏项与语句分类）</h2>')
    for item in manifest.get('extraction_audit', []):
        parts.append('<p>' + escape(item.get('body_location', '') + ' / ' + item['reason'])
                     + '</p><pre>' + escape(item['claim']) + '</pre>')
    parts.append('<h2>正文实际引用来源的已保存原文</h2>')
    for index, source in enumerate(sources):
        parts.append(f'<section id="source-{index}"><h3>' + escape(str(source.get('title') or source.get('locator', ''))) + '</h3>')
        locator = str(source.get('locator') or source.get('file_name', ''))
        parts.append('<p>' + escape(locator) + '</p>')
        if _public_link(locator):
            parts.append(f'<a href="{escape(locator, quote=True)}" rel="noreferrer">打开原网页</a>')
        local_path = Path(source.get('source_path') or '')
        if (local_path.is_absolute() and local_path.resolve().is_relative_to((root / 'local_docs').resolve())
                and local_path.suffix.lower() in {'.pdf', '.docx', '.txt', '.md', '.xlsx'} and local_path.is_file()):
            parts.append(f'<a href="{escape(local_path.as_uri(), quote=True)}">打开本机原文件</a>')
        for page in source.get('pages', []):
            parts.append('<h4>原文位置：' + escape(str(page.get('page') or '未记录页码')) + '</h4><pre>'
                         + escape(str(page.get('text', ''))) + '</pre>')
        parts.append('</section>')
    return '\n'.join(parts)


def create_review_package(record, project_root=None):
    root = _root(project_root)
    identity, report = report_identity(record, root)
    catalog = record.get('original_source_catalog') or {'sources': []}
    audit = []
    rows = extract_published_rows(report, record.get('citation_map') or {}, catalog, audit=audit)
    rows, audit, cleanup = clean_review_rows(rows, audit)
    catalog = cited_catalog(rows, catalog)
    if max(len(rows), len(audit)) >= MAX_ROWS:
        raise ManualReviewError('正文核验项超过核验表上限，需要拆分任务；未静默截断样本。')
    manifest = {**identity, 'policy_version': POLICY_VERSION, 'package_id': uuid.uuid4().hex,
                'extraction_schema': 'object-attribute-v3', 'extraction_audit': audit,
                'export_cleanup': cleanup,
                'deduplication': '同对象、属性、数值、单位、条件及可见分句去重；出现位置与引用保留，不跨语义猜测合并',
                'selection_scope': 'published_body_only',
                'public_url_count': len({r['source'] for r in rows if r['kind'] == 'source'}),
                'run_id': record['run_id'], 'task': record.get('task', ''),
                'report_title': record.get('report_title', ''), 'created_at': _now(),
                'started_at': record.get('started_at', ''), 'completed_at': record.get('completed_at', ''),
                'duration_seconds': record.get('total_duration_seconds'),
                'model': json.dumps(record.get('model_provider', {}), ensure_ascii=False),
                'export_complete': bool(record.get('export_status')) and all(record['export_status'].values()),
                'report_status': record.get('status', ''), 'rows': rows}
    folder = _run_folder(record['run_id'], root) / manifest['package_id']
    folder.mkdir(parents=True, exist_ok=False)
    # Evidence is preserved once per package, so later retrieval cannot change the audit basis.
    save_snapshot(folder, 'sources', catalog)
    (folder / 'evidence.html').write_text(_materials_html(manifest, report, catalog, root), encoding='utf-8')
    (folder / 'report.md').write_text(report, encoding='utf-8')
    write_workbook(manifest, folder / '人工指标核验表.xlsx')
    manifest['immutable_files'] = {name: _digest((folder / name).read_bytes())
                                   for name in ('sources.json', 'evidence.html', 'report.md')}
    save_snapshot(folder, 'manifest', manifest)
    return {'status': 'pending', 'package_id': manifest['package_id'],
            'report_sha256': identity['report_sha256'],
            'workbook': str(folder / '人工指标核验表.xlsx'), 'materials': str(folder / 'evidence.html'),
            'entity_count': sum(r['kind'] == 'entity' for r in rows),
            'source_count': sum(r['kind'] == 'source' for r in rows),
            'public_url_count': manifest['public_url_count'],
            'export_cleanup': manifest['export_cleanup'],
            'selection_scope': manifest['selection_scope']}


def _normalized(value):
    return re.sub(r'\s+', '', str(value))


def _claim_text(value):
    value = re.sub(r'\[([^\n]*?)\]\(#ref-[^)]+\)', r'\1', str(value))
    return _normalized(REF.sub('', value)).replace('**', '').replace('`', '')


def score_rows(rows, complete):
    result = {}
    for kind, threshold in [('entity', .9), ('source', .98)]:
        counts = Counter(r['decision'] for r in rows if r['kind'] == kind)
        correct, incorrect, pending = (counts[d] for d in ('正确', '错误', '待核验'))
        total = correct + incorrect + pending
        finished = complete and total > 0 and pending == 0
        accuracy = correct / total if finished else None
        result[kind] = {'total': total, 'correct': correct, 'incorrect': incorrect,
                        'pending': pending, 'excluded': counts['排除'], 'threshold': threshold,
                        'accuracy': accuracy, 'reviewed_accuracy': correct / (correct + incorrect) if correct + incorrect else None,
                        'requirement_met': accuracy >= threshold if accuracy is not None else None,
                        'status': 'no_samples' if not total else 'completed' if finished else 'pending'}
    return result


def _validate_import(record, content, root):
    sheets = read_workbook(content)
    meta_rows = sheets['任务与规则'][1:]
    if len({r[0] for r in meta_rows}) != len(meta_rows):
        raise ManualReviewError('任务信息包含重复字段。')
    meta = {r[0]: r[1] if len(r) > 1 else '' for r in meta_rows}
    package_id = meta.get('核验包ID', '')
    if not re.fullmatch('[a-f0-9]{32}', package_id):
        raise ManualReviewError('核验包ID无效。')
    folder = _run_folder(record['run_id'], root) / package_id
    try:
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ManualReviewError('未找到该运行对应的核验包，请重新导出。') from exc
    if manifest.get('policy_version') not in {POLICY_VERSION, 'human-acceptance-v1', 'human-acceptance-v2-published-body'}:
        raise ManualReviewError('核验规则版本不匹配，请重新导出。')
    if meta.get('运行编号') != record['run_id'] or meta.get('报告SHA256') != manifest['report_sha256']:
        raise ManualReviewError('运行编号或报告哈希不匹配。')
    identity, report = report_identity(record, root)
    if any(identity[k] != manifest[k] for k in ('report_sha256', 'artifacts')):
        raise ManualReviewError('报告版本或导出文件已经变化，旧核验表不能复用。')
    for name, checksum in manifest['immutable_files'].items():
        if not (folder / name).is_file() or _digest((folder / name).read_bytes()) != checksum:
            raise ManualReviewError('核验包证据快照发生变化，请重新导出。')
    rows, ids = [], set()
    structured = manifest['policy_version'] == POLICY_VERSION
    if structured:
        if manifest.get('extraction_schema') != 'object-attribute-v3':
            raise ManualReviewError('结构化提取版本缺失，请重新导出。')
        expected_audit = [AUDIT_HEADERS] + [audit_values(item, index)
                         for index, item in enumerate(manifest.get('extraction_audit', []), 1)]
        if sheets.get('提取审计') != expected_audit:
            raise ManualReviewError('提取审计被修改或缺失，请保留原始审计记录。')
    elif '提取审计' in sheets:
        raise ManualReviewError('旧版核验包工作表不匹配。')
    expected = {r['id']: r for r in manifest['rows']}
    for sheet_name, kind in [('实体核验', 'entity'), ('溯源核验', 'source'), ('漏项与争议', None)]:
        sheet = sheets[sheet_name]
        headers = (V3_HEADERS if structured else LEGACY_HEADERS
                   if manifest['policy_version'] == 'human-acceptance-v1' else HEADERS)
        if not sheet or sheet[0] != headers:
            raise ManualReviewError(f'{sheet_name}表头发生变化。')
        for values in sheet[1:]:
            if not any(values):
                continue
            values = values + [''] * (len(headers) - len(values))
            row_id = values[0]
            if not row_id or row_id in ids:
                raise ManualReviewError('样本编号为空或重复。')
            ids.add(row_id)
            if kind:
                original = expected.get(row_id)
                if not original or original['kind'] != kind or values[:9] != fixed_values(original):
                    raise ManualReviewError(f'{row_id}的只读字段被修改或所属工作表错误。')
                if structured and values[13:] != fact_values(original):
                    raise ManualReviewError(f'{row_id}的结构化只读字段被修改。')
                item = dict(original)
            else:
                if not re.fullmatch(r'A[0-9A-Za-z_-]+', row_id) or row_id in expected:
                    raise ManualReviewError('补充样本须使用A开头的唯一编号。')
                row_kind, separator, category = values[1].partition(':')
                if row_kind not in {'entity', 'source'} or not separator or not category.strip():
                    raise ManualReviewError('补充样本类别须为entity:类别或source:公开引用。')
                if not values[2].strip() or not values[3].strip():
                    raise ManualReviewError('补充样本缺少实体名称或报告原句。')
                body = re.split(r'^#{1,6}\s*参考文献\s*$', report, maxsplit=1, flags=re.M)[0]
                # Citation punctuation can differ between the displayed report and the review view.
                if _claim_text(values[3]) not in _claim_text(body):
                    raise ManualReviewError(f'{row_id}的原句无法在最终报告正文定位。')
                if row_kind == 'source' and not _public_link(values[5]):
                    raise ManualReviewError('公开引用补充项必须填写对应HTTP(S) URL。')
                item = {'id': row_id, 'kind': row_kind, 'category': category, 'name': values[2],
                        'claim': values[3], 'source': values[5], 'added_manually': True}
            decision, reason, quote, location = [v.strip() for v in values[9:13]]
            decision = decision or '待核验'
            if decision not in DECISIONS:
                raise ManualReviewError(f'{row_id}判定无效。')
            if decision != '待核验' and not reason:
                raise ManualReviewError(f'{row_id}须填写判定理由。')
            if decision == '正确' and (not quote or not location):
                raise ManualReviewError(f'{row_id}正确项须填写核验原文摘录及来源位置。')
            item.update(decision=decision, reason=reason, quote=quote, location=location)
            rows.append(item)
    if not set(expected).issubset(ids):
        raise ManualReviewError('原始核验行被删除，不能缩小分母；请保留并填写判定。')
    seen = set()
    for row in rows:
        if row['decision'] == '排除':
            continue
        key = (row['kind'], _normalized(row['name']), _normalized(row['claim']), row.get('source', ''),
               row.get('attribute', ''), row.get('value', ''), row.get('unit', ''), row.get('conditions', ''))
        if key in seen:
            raise ManualReviewError('存在重复计分项，请在重复行说明原因并排除。')
        seen.add(key)
    excluded = any(r['decision'] == '排除' for r in rows)
    if not meta.get('核验人', '').strip():
        raise ManualReviewError('请填写核验人。')
    try:
        datetime.strptime(meta.get('核验日期', '')[:10], '%Y-%m-%d')
    except ValueError as exc:
        raise ManualReviewError('核验日期请填写YYYY-MM-DD。') from exc
    if any(meta.get(field, '') not in {'', '确认'} for field in ('全量检查确认', '排除项复核确认', '提取复核确认')):
        raise ManualReviewError('确认字段仅接受“确认”或留空。')
    exclusions_confirmed = not excluded or (meta.get('排除项复核确认') == '确认'
        and bool(meta.get('复核人', '').strip()) and meta['复核人'].strip() != meta['核验人'].strip())
    audit_confirmed = not structured or not manifest.get('extraction_audit') or meta.get('提取复核确认') == '确认'
    complete = meta.get('全量检查确认') == '确认' and exclusions_confirmed and audit_confirmed
    if manifest.get('export_cleanup', {}).get('missing_source_rows'):
        complete = False  # A sourced subset cannot certify the entire published report.
    return manifest, rows, meta, complete, folder


def _seconds(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def assess_manual_result(result):
    statuses = [result['duration']['requirement_met'], result['entity']['requirement_met'], result['source']['requirement_met']]
    if False in statuses:
        return '未达标'
    if any(s is None for s in statuses) or not result['export_complete'] or result.get('report_status') == 'draft':
        return '待完成'
    return '达标'


def _cell(value):
    value = str(value if value is not None else '未记录')
    return escape(value).replace('|', '／').replace('\n', ' ').replace('`', '').replace('*', '').replace('[', '［').replace(']', '］')


def render_manual_report(result):
    lines = ['# 人工指标测评报告', '', f"任务：{_cell(result['task'])}", '',
             f"运行编号：{_cell(result['run_id'])}", '', f"报告SHA256：{result['report_sha256']}", '',
             f"核验人：{_cell(result['reviewer'])}；复核人：{_cell(result['second_reviewer'])}；核验日期：{_cell(result['review_date'])}", '',
             f"总体判定：{result['overall']}。全量检查确认：{'是' if result['coverage_confirmed'] else '否'}。", '',
             '准确率按正确数/纳入总数计算；待核验不算错误，也不作达标判定。排除项保留审计。未使用模型裁判。', '',
             '| 指标 | 总数 | 正确 | 错误 | 待核验 | 排除 | 准确率 | 要求 | 判定 |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for kind, label in [('entity', '核心技术实体'), ('source', '公开引用关系')]:
        s = result[kind]
        rate = f"{s['accuracy']:.2%}" if s['accuracy'] is not None else '无样本' if not s['total'] else '待完成'
        verdict = '达标' if s['requirement_met'] is True else '未达标' if s['requirement_met'] is False else '待完成'
        lines.append(f"| {label} | {s['total']} | {s['correct']} | {s['incorrect']} | {s['pending']} | {s['excluded']} | {rate} | {s['threshold']:.0%} | {verdict} |")
    seconds = result['duration']['seconds']
    lines += ['', f"生成耗时：{seconds if seconds is not None else '未记录'}秒；要求不超过1200秒。人工核验时间另计。",
              '', '本表按最终报告中事实项核验，不评价漏抽召回率或F1；链接可访问性不等于引用准确率。', '',
              '## 逐项核验记录', '']
    missing = result.get('export_cleanup', {}).get('missing_source_rows', 0)
    if missing:
        lines += [f'导出时移除了{missing}个来源为空的核验项；本表仅覆盖保留样本，不能据此认定全报告达标。', '']
    for r in result['rows']:
        lines += [f"### {_cell(r['id'])} {_cell(r['name'])}", '',
                  f"类别：{'实体' if r['kind'] == 'entity' else '公开引用'}；人工判定：{_cell(r['decision'])}。", '',
                  '报告原句：' + _cell(r['claim']), '', '判定理由：' + _cell(r['reason']), '',
                  '核验摘录：' + _cell(r['quote']), '', '来源及位置：' + _cell(r['location']), '']
    lines += ['', '## 异常与争议说明', '', _cell(result.get('notes', '') or '未填写'), '',
              '至少10轮测试且每轮三项指标均达标，方可形成该组测试的指标达标结论。']
    return '\n'.join(lines)


def import_review(record, content, project_root=None):
    root = _root(project_root)
    manifest, rows, meta, complete, folder = _validate_import(record, content, root)
    seconds = _seconds(manifest.get('duration_seconds'))
    result = {'mode': 'human', 'review_id': uuid.uuid4().hex, 'package_id': manifest['package_id'],
              'policy_version': manifest['policy_version'], 'run_id': record['run_id'], 'task': manifest['task'],
              'report_sha256': manifest['report_sha256'], 'artifacts': manifest['artifacts'],
              'reviewer': meta['核验人'], 'second_reviewer': meta.get('复核人', ''),
              'review_date': meta['核验日期'], 'imported_at': _now(), 'coverage_confirmed': complete,
              'extraction_audit_confirmed': (not manifest.get('extraction_audit')
                                             or meta.get('提取复核确认') == '确认'),
              'notes': meta.get('异常或争议说明', ''), 'export_complete': manifest['export_complete'],
              'report_status': manifest['report_status'], 'rows': rows,
              'export_cleanup': manifest.get('export_cleanup', {}),
              'duration': {'seconds': seconds, 'threshold': 1200, 'requirement_met': seconds <= 1200 if seconds is not None else None},
              **score_rows(rows, complete)}
    result['overall'] = assess_manual_result(result)
    destination = folder / result['review_id']
    destination.mkdir(exist_ok=False)
    (destination / '已填写核验表.xlsx').write_bytes(content)
    markdown = render_manual_report(result)
    md_path, word_path = destination / '人工指标测评报告.md', destination / '人工指标测评报告.docx'
    md_path.write_text(markdown, encoding='utf-8')
    result['paths'] = {'markdown': str(md_path), 'word': ''}
    try:
        from backend.reporting.document_export import render_word
        render_word(markdown, word_path, base_path=root)
        result['paths']['word'] = str(word_path)
    except Exception as exc:
        result['export_error'] = f'Word导出失败（{type(exc).__name__}），核验结果及Markdown已保存。'
    save_snapshot(destination, 'result', result)
    return result


def summarize_runs(records, project_root=None):
    runs, seen = [], set()
    for record in records:
        run_id = record['run_id']
        if run_id in seen:
            raise ManualReviewError('汇总不能重复选择同一运行。')
        seen.add(run_id)
        reviews = record.get('manual_reviews') or []
        latest = reviews[-1] if reviews else None
        status = '待核验'
        if latest:
            try:
                identity, _ = report_identity(record, project_root)
                current = (latest.get('run_id') == run_id and latest.get('mode') == 'human'
                           and latest.get('policy_version') == POLICY_VERSION
                           and all(identity[k] == latest[k] for k in ('report_sha256', 'artifacts')))
            except ManualReviewError:
                current = False
            status = latest['overall'] if current else '版本变化，需重新核验'
        runs.append({'run_id': run_id, 'task': record.get('task'), 'status': status,
                     'review_id': latest.get('review_id') if latest else None,
                     'duration': latest.get('duration') if latest else None,
                     'entity': latest.get('entity') if latest else None, 'source': latest.get('source') if latest else None})
    return {'policy_version': POLICY_VERSION, 'rounds': len(runs), 'required_rounds': 10,
            'requirement_met': len(runs) >= 10 and all(r['status'] == '达标' for r in runs), 'runs': runs}


def export_round_summary(summary, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    lines = ['# 人工指标测评多轮汇总', '',
             f"测试轮次：{summary['rounds']}；要求至少10轮且每轮达标；本组{'达标' if summary['requirement_met'] else '未满足全部要求'}。", '',
             '| 运行编号 | 任务 | 耗时（秒） | 实体准确率 | 溯源准确率 | 判定 |',
             '| --- | --- | --- | --- | --- | --- |']
    for run in summary['runs']:
        def rate(kind):
            value = (run.get(kind) or {}).get('accuracy')
            return f'{value:.2%}' if value is not None else '待完成'
        lines.append('| ' + ' | '.join(_cell(v) for v in [run['run_id'], run['task'],
                     (run.get('duration') or {}).get('seconds'), rate('entity'), rate('source'), run['status']]) + ' |')
    markdown = '\n'.join(lines)
    md, word = folder / '多轮人工指标测评汇总.md', folder / '多轮人工指标测评汇总.docx'
    md.write_text(markdown, encoding='utf-8')
    result = {'markdown': str(md), 'word': ''}
    try:
        from backend.reporting.document_export import render_word
        render_word(markdown, word, base_path=folder)
        result['word'] = str(word)
    except Exception as exc:
        result['error'] = f'Word导出失败（{type(exc).__name__}），Markdown和JSON已保存。'
    return result
