"""Offline regression replay. Does not fetch URLs or rewrite the original report."""
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.reporting.source_grounding import build_source_catalog, pack_sources
from backend.reporting.web_source_tracking import build_web_source_tracking
from gpt_researcher.evaluation.evidence_workspace import stage_catalog
from gpt_researcher.retrievers.web_evidence_policy import build_topic_queries

stats_path = next((ROOT / 'outputs').glob('*af0e_指标测评.json'))
stats = json.loads(stats_path.read_text(encoding='utf-8'))['run_statistics']
workspace = ROOT / 'outputs/records/evidence' / stats['run_id']
sections = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(workspace.glob('retrieval_*.json'))]
old_catalog = stats['original_source_catalog']
catalog = build_source_catalog([], sections, allow_web=True, query=stats['task'], allow_fetch=False)
catalog['sources'] = copy.deepcopy([s for s in old_catalog['sources'] if s.get('kind') == 'local']) + catalog['sources']
catalog = stage_catalog(catalog)
packed = pack_sources(catalog, stats['task'], budget=50000)
published = json.loads((workspace / 'final_report.json').read_text(encoding='utf-8'))['published_markdown']
tracking = build_web_source_tracking(catalog, stats['citation_map'], published)
old_urls = {s['locator'] for s in old_catalog['sources'] if s.get('kind') == 'web'}
raw_urls = {p['url'] for section in sections for p in section.get('web_evidence', [])}
new_urls = {s['locator'] for s in catalog['sources'] if s.get('kind') == 'web'}
assert raw_urls <= new_urls
assert len(packed) <= 50000
queries = [build_topic_queries(stats['task'], s['subtopic'], stats['query_domains']) for s in stats['research_sections']]
restored = [s for s in tracking['sources'] if s['url'] not in old_urls and s['writing_selected']]
result = {'mode':'offline_replay', 'original_run_id':stats['run_id'],
          'note':'使用历史抓取文本回放；未联网检索、未重写报告、未重新核验结论。引用状态沿用旧正文；旧快照未记录的搜索候选及失败URL无法补算。',
          'previous_catalog_web_sources':len(old_urls), 'saved_unique_urls':len(raw_urls),
          'retained_saved_urls':len(raw_urls & new_urls), 'writing_context_characters':len(packed),
          'new_query_plans':queries, 'restored_selected_sources':restored, 'web_source_tracking':tracking}
stem = ROOT / 'outputs/af0e_网页来源流程回放'
stem.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
summary = tracking['summary']
lines = ['# af0e 网页来源流程回放', '', result['note'], '',
         '| 项目 | 数量 |', '| --- | ---: |',
         f'| 历史抓取保存的去重URL | {len(raw_urls)} |',
         f'| 旧最终来源目录的网页 | {len(old_urls)} |',
         f'| 本次完整保留的历史抓取URL | {len(raw_urls & new_urls)} |',
         f'| 规则初筛通过，可供写作选择 | {summary["eligible"]} |',
         f'| 规则初筛排除 | {summary["excluded"]} |',
         f'| 本次回放送入写作的网页 | {summary["writing_selected"]} |',
         f'| 从旧目录遗漏部分恢复并选入的网页 | {len(restored)} |',
         f'| 写作原文总字符数（含本地材料） | {len(packed)} |', '',
         '发布日期缺失的网页保留“未知”；规则初筛通过不代表事实核验通过。旧报告引用数不能用来预测重新生成后的引用数。', '',
         '## 恢复并选入的来源', '']
lines += [f'- [{s["title"]}]({s["url"]})' for s in restored]
lines += ['', '## 六个专题的新查询示例', '']
for index, plan in enumerate(queries, 1):
    lines += [f'### 专题 {index}', ''] + [f'- {q}' for q in plan] + ['']
lines += ['## 每个来源的处理结果', '', '| 标题 | 送入写作 | 旧正文引用 | 原因 |', '| --- | --- | --- | --- |']
def cell(value):
    return str(value).replace('|', '／').replace('\n', ' ')
for row in tracking['sources']:
    lines.append(f'| [{cell(row["title"])}]({row["url"]}) | {"是" if row["writing_selected"] else "否"} | {"是" if row["cited"] else "否"} | {cell(row["reason"])} |')
stem.with_suffix('.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in {'new_query_plans', 'restored_selected_sources', 'web_source_tracking'}}, ensure_ascii=False))
print(json.dumps(summary, ensure_ascii=False))
print('restored_selected_sources:', len(restored))
print(stem.with_suffix('.md'))
