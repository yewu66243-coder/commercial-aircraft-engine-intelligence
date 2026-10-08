"""Offline reproduction of df3d's missing body citations; no model verdicts."""
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.reporting.body_citations import citation_coverage, coverage_summary, repair_body_citations
from backend.reporting.formal_report import prepare_formal_report


async def main():
    stats = json.loads(next((ROOT/'outputs').glob('*df3d_指标测评.json')).read_text(encoding='utf-8'))['run_statistics']
    workspace = ROOT/'outputs/records/evidence'/stats['run_id']
    writer = json.loads((workspace/'writer_completion.json').read_text(encoding='utf-8'))['content']
    catalog = stats['original_source_catalog']
    local = [s for s in catalog['sources'] if s.get('kind') == 'local']
    prompts = []
    async def offline_model(prompt):
        packet = json.loads(prompt.split('材料：\n')[1])
        prompts.append({'paragraph_ids':[p['id'] for p in packet['paragraphs']],
                        'candidate_passages':len(packet['passages'])})
        # Test that no attribution is invented when there is no model decision.
        return json.dumps({'bindings':[]})
    unchanged, audit = await repair_body_citations(writer, local, catalog, offline_model)
    assert unchanged == writer
    assert audit['status'] == 'needs_review'
    assert len(prompts) == 3
    final_editor = stats['editorial_review']['rounds'][-1]['report']
    prepared = prepare_formal_report(final_editor, stats['task'], local, metadata={'source_catalog':catalog})
    coverage = citation_coverage(writer, local, catalog['sources'])
    assert coverage['cited_paragraph_count'] == 0
    assert coverage['uncited_fact_paragraph_count'] == 24
    assert not prepared.quality['body_citation_coverage']['passed']
    result = {
        'mode':'offline_regression_no_model_calls', 'original_run_id':stats['run_id'],
        'note':'使用df3d真实首稿及已保存原文验证缺陷识别、补写请求和导出告警；未联网、未改写旧报告、未自动判定来源支撑。',
        'writer_coverage':coverage_summary(coverage), 'repair_request_batches':prompts,
        'unresolved_preserves_original':unchanged == writer,
        'unresolved_status':audit['status'],
        'export_body_citation_coverage':prepared.quality['body_citation_coverage'],
        'paragraphs_needing_review':[{'id':p['id'], 'section':p['section'], 'text':p['visible']} for p in coverage['targets']],
    }
    stem = ROOT/'outputs/df3d_正文引用检查回放'
    stem.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# df3d 正文引用检查回放', '', result['note'], '',
             '| 检查项 | 结果 |', '| --- | --- |',
             '| 首稿正式正文中建立来源关联的段落 | 0 |',
             '| 缺少引用关联的重要事实段落或表格 | 24 |',
             '| 新流程准备的原文核对与补写请求 | 3 批，每批8段 |',
             '| 无模型判定时 | 原稿保持不变，状态为需复核，不虚构引用 |',
             '| 最终排版检查 | 能识别图源之外的正式正文仍缺引用 |', '',
             '补写仅允许引用本轮已保存原文，模型须返回逐字证据；程序检查原文定位后插入引用编号。该步骤只建立引用关联，事实支撑仍需后续审校和独立核验。', '',
             '原df3d文档未在此次回放中被修改。新报告生成时会执行实际补写；找不到依据时保留缺口，不自动增加参考文献凑数。', '',
             '## 本轮识别出的缺引用位置', '']
    for p in coverage['targets']:
        lines += [f'- {p["section"]}：{p["visible"][:100].replace(chr(10), " ")}…']
    stem.with_suffix('.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'missing_paragraphs':24, 'repair_batches':len(prompts),
                      'status':audit['status'], 'export_catches_missing_body_citations':True}, ensure_ascii=False))
    print(stem.with_suffix('.md'))


if __name__ == '__main__':
    asyncio.run(main())
