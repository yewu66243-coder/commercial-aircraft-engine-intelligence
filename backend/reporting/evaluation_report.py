"""Per-run measurement report; heuristic checks are not acceptance verdicts."""
from __future__ import annotations

import json
import math
from pathlib import Path
from urllib.parse import quote

TIME_LIMIT_SECONDS = 20 * 60


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _cell(value):
    return str(value if value is not None else "未记录").replace("|", "／").replace("\n", " ").replace("\r", " ")


def _percent(value):
    value = _number(value)
    return f"{value:.2%}" if value is not None else "无法计算"


def assess_run(record):
    seconds = _number(record.get("total_duration_seconds"))
    entity = record.get("entity_eval") or {}
    source = record.get("public_url_source_eval") or {}
    auto = entity.get('auto_evidence_eval') or {}
    entity_score = entity.get('accuracy')
    entity_method = "标准答案名称匹配初筛；尚未独立核对数值、单位及语义，不能作为正式验收结论"
    if entity_score is None:
        entity_score = auto.get('strict_auto_evidence_accuracy') if auto.get('checked_count', 0) else None
        entity_method = "正文或内部表格候选的原文证据初筛；分母包含未核验候选，部分支撑计0.5分，不评价漏抽，不等同正式实体准确率"
    entity_value = _percent(entity_score) if entity_score is not None else (
        '已提取候选，但未能读取对应证据' if entity.get('extracted_count', 0) else '未识别到实体或参数候选')
    source_value = _percent(source.get('support_accuracy')) if source.get('support_accuracy') is not None else (
        '本轮未引用公开网页，无链接样本' if not source.get('cited_url_ref_count', 0) else '公开链接未完成核验')
    # Existing evaluators use name/token matching, not an independent judge.
    metrics = [
        {"id": "duration", "name": "深度检索及报告生成耗时", "threshold": "不超过20分钟",
         "value": f"{seconds / 60:.2f}分钟（{seconds:g}秒）" if seconds is not None else "未记录",
         "status": "待复核" if seconds is None else "达标" if seconds <= TIME_LIMIT_SECONDS else "未达标",
         "method": "后端任务开始至研究报告导出完成；不含浏览器网络传输和测评附件导出时间"},
        {"id": "entity", "name": "核心技术实体抽取准确率", "threshold": "不低于90%",
         "value": entity_value, "status": "待复核",
         "method": entity_method},
        {"id": "source", "name": "公开信息溯源链接准确率", "threshold": "不低于98%",
         "value": source_value, "status": "待复核",
         "method": "现有规则按引用上下文关键词及数字匹配，部分支撑计0.5分，未核验计入分母；须由人工或模型裁判复核"},
    ]
    for item, score, threshold in ((metrics[1], entity_score, 0.9),
                                   (metrics[2], source.get("support_accuracy"), 0.98)):
        value = _number(score)
        item["screening_status"] = "待补充测评条件" if value is None else "初筛达标" if value >= threshold else "初筛未达标"
    judge = record.get('independent_judge') or {}
    if judge.get('status') in {'completed', 'partial'}:
        for item, kind in ((metrics[1], 'entity'), (metrics[2], 'source')):
            measured = judge.get(kind) or {}
            if not measured.get('total'):
                continue
            item.pop('screening_status', None)
            verified = measured.get('correct', 0) + measured.get('incorrect', 0)
            item['value'] = (_percent(measured.get('accuracy')) + '（模型核验）'
                             if verified else f"未完成核验（{measured['total']}项待核验）")
            met = measured.get('requirement_met')
            item['status'] = '待复核' if not verified else '达标' if met is True else '未达标' if met is False else '待复核'
            item['method'] = (
                f"独立模型{judge.get('model', '')}逐项核验；正确{measured.get('correct', 0)}/总数{measured['total']}，"
                f"待核验{measured.get('unresolved', 0)}；待核验项保留在分母中，未全部核验时不作达标判定。"
                + measured.get('scope', ''))
    statuses = [item['status'] for item in metrics]
    overall = "未达标" if "未达标" in statuses else "待复核" if "待复核" in statuses else "达标"
    return {"overall": overall, "metrics": metrics,
            "acceptance_ready": overall == "达标", "required_test_rounds": 10}


def render_evaluation_report(record, assessment):
    entity = record.get("entity_eval") or {}
    auto = entity.get("auto_evidence_eval") or {}
    source = record.get("public_url_source_eval") or {}
    quality = record.get("report_quality") or {}
    lines = ["# 情报报告单次指标测评报告", "",
             f"关联报告：{_cell(record.get('report_title'))}", "",
             f"任务：{_cell(record.get('task'))}", "",
             f"运行编号：{_cell(record.get('run_id'))}", "",
             f"开始时间：{_cell(record.get('started_at'))}；研究报告完成时间：{_cell(record.get('completed_at'))}", "",
             f"本轮综合判定：{assessment['overall']}。本表依据任务说明书的20分钟、90%、98%要求生成。", "",
             "## 1 指标与判定", "",
             "| 指标 | 要求 | 实测或初筛结果 | 判定 |", "| --- | --- | --- | --- |"]
    for item in assessment["metrics"]:
        verdict = item['status'] if 'screening_status' not in item else item['screening_status'] + '；验收待复核'
        lines.append(f"| {item['name']} | {item['threshold']} | {item['value']} | {verdict} |")
    lines += ["", "## 2 测评方法与样本", ""]
    for item in assessment["metrics"]:
        lines += [f"{item['name']}：{item['method']}。", ""]
    gold = _percent(entity.get('accuracy')) if entity.get('accuracy') is not None else '未提供标准答案，未执行标准答案比对'
    screening = _percent(auto.get('strict_auto_evidence_accuracy')) if auto.get('checked_count', 0) else '未完成原文核验'
    coverage = _percent(auto.get('checked_count', 0) / entity['extracted_count']) if entity.get('extracted_count') else '无候选样本'
    lines += [f"实体/参数候选数：{entity.get('extracted_count', 0)}；标准答案名称匹配：{gold}；"
              f"原文证据初筛（包含未核验样本）：{screening}。", "",
              f"已核验 {auto.get('checked_count', 0)} 项，未核验 {auto.get('unchecked_count', 0)} 项；完全命中 {auto.get('supported_count', 0)}，"
              f"部分命中 {auto.get('partially_supported_count', 0)}，未命中 {auto.get('unsupported_count', 0)}。", "",
              f"原文核验覆盖率：{coverage}。同一实体在不同语句中分别计为候选项；无引用实体会在本轮原文库检索证据，不自动继承其他句子的来源；检索命中不代表事实正确。词项命中不等同于语义事实正确。", "",
              f"实体测评说明：{_cell(entity.get('note', '未获得有效实体测评结果'))}", "",
              f"公开链接样本数：{source.get('cited_url_ref_count', 0)}；完全支撑 {source.get('supported_count', 0)}，"
              f"部分支撑 {source.get('partially_supported_count', 0)}，未支撑 {source.get('unsupported_count', 0)}，"
              f"未核验 {source.get('unchecked_count', 0)}。", "",
              f"链接可访问率：{_percent((record.get('url_check') or {}).get('accessibility_rate')) if (record.get('url_check') or {}).get('total_urls', 0) else '未执行访问测试'}，仅用于诊断网络访问。", "",
              "无实体样本、无公开链接样本或缺少裁判结果时，不以100%或零分代替测量结果。", "",
              "## 3 复核事项", ""]
    if assessment["metrics"][0]["status"] == "未达标":
        lines += ["本轮耗时超过20分钟，应根据阶段耗时定位检索、模型调用、审校或导出瓶颈。", ""]
    issues = list(quality.get("warnings") or []) + list(record.get("export_errors") or [])
    for issue in issues:
        lines += [f"- {_cell(issue)}"]
    if not issues:
        lines += ["自动结构检查未记录额外问题。"]
    lines += ["", "请对照来源逐项核对实体名称、数值、单位、适用范围和引用结论；复核结论应记录裁判、时间与依据。", "",
              "## 4 公开链接核验明细", "",
              "| 引用 | 初筛状态 | 原因 |", "| --- | --- | --- |"]
    for item in source.get("results") or []:
        lines.append(f"| {_cell(item.get('ref'))} | {_cell(item.get('status'))} | {_cell(item.get('reason'))} |")
    if not source.get("results"):
        lines.append("| 无公开链接样本 | 本项未测 | 本轮仅有本地引用时，请查看下列本地原文关联情况；不以本地关联率替代98%公开链接指标 |")
    local = record.get('local_reference_eval') or {}
    lines += ['', '### 4.1 本地原文关联', '',
              f"本地来源编号数：{local.get('cited_count', 0)}；已关联可读原文：{local.get('readable_count', 0)}；"
              f"可追溯率：{_percent(local.get('readable_rate')) if local.get('cited_count') else '无本地引用样本'}。该比例只表示引用可以找到原文，不代表事实正确率。", '',
              '| 引用 | 来源 | 结果 |', '| --- | --- | --- |']
    for item in local.get('results') or []:
        lines.append(f"| {_cell(item.get('ref'))} | {_cell(item.get('source'))} | {_cell(item.get('reason'))} |")
    lines += ['', '### 4.2 实体及参数复核样例', '',
              '以下展示前20项，全部候选、逐项判定、引用和命中原文片段保存在关联JSON。', '',
              '| 候选 | 初筛状态 | 原因 |', '| --- | --- | --- |']
    labels = {'supported': '词项命中', 'partially_supported': '部分命中', 'unsupported': '未命中', 'unchecked': '未核验'}
    for item in (entity.get('extracted_entities') or [])[:20]:
        check = item.get('auto_evidence_check') or {}
        lines.append(f"| {_cell(item.get('name'))} | {labels.get(check.get('status'), '未核验')} | {_cell(check.get('reason'))} |")
    judge = record.get('independent_judge') or {}
    lines += ['', '### 4.3 独立大模型裁判', '',
              f"裁判模型：{_cell(judge.get('model') or '未配置')}；状态：{_cell(judge.get('status') or '未启用')}；"
              f"耗时：{_cell(judge.get('duration_seconds'))}秒。", '',
              _cell(judge.get('message') or '此运行未执行独立裁判。'), '',
              '独立裁判按本轮保存的原文核验，不代表来源本身绝对真实；实体指标覆盖已抽取候选，不评价漏抽。完整逐项结论和证据片段见JSON。', '',
              '| 类型 | 总数 | 正确 | 错误 | 待核验 |', '| --- | --- | --- | --- | --- |']
    for kind, label in (('entity', '实体/参数候选'), ('source', '公开URL编号')):
        measured = judge.get(kind) or {}
        lines.append(f"| {label} | {measured.get('total', 0)} | {measured.get('correct', 0)} | {measured.get('incorrect', 0)} | {measured.get('unresolved', 0)} |")
    labels = {'correct': '正确', 'incorrect': '错误', 'insufficient': '证据不足', 'unverified': '未核验'}
    rows = judge.get('results') or []
    if record.get('evidence_workspace'):
        lines += ['', f"本轮证据暂存位置：{_cell(record['evidence_workspace'])}。保存检索片段、原文页码、最终正文评分样本和裁判记录。", '',
                  f"扩大原文后复核的样本数：{sum(len(row.get('attempts', [])) > 1 for row in rows)}。正确、错误和待核验分别统计；待核验项仍保留在分母中。", '']
    if rows:
        lines += ['', '以下列出前20项裁判结果，优先列出错误和待核验项。', '',
                  '| 编号 | 对象 | 判定 | 理由 |', '| --- | --- | --- | --- |']
        for row in sorted(rows, key=lambda r: r.get('verdict') == 'correct')[:20]:
            lines.append(f"| {_cell(row.get('id'))} | {_cell(row.get('name'))} | {labels.get(row.get('verdict'), '未核验')} | {_cell(row.get('reason'))} |")
    diagnostics = record.get('search_diagnostics') or []
    if diagnostics:
        lines += ['', '### 4.4 网络检索诊断', '', '| 服务 | 状态 | HTTP状态 | 说明 |', '| --- | --- | --- | --- |']
        seen = set()
        for event in diagnostics:
            key = (event.get('provider'), event.get('status'), event.get('http_status'), event.get('message'))
            if key in seen:
                continue
            seen.add(key)
            lines.append('| ' + ' | '.join(_cell(value) for value in key) + ' |')
        lines += ['', '完整查询、返回数量及抓取情况保存在本轮研究记录；搜索返回链接不等同于已取得支持正文的原文。', '']
    tracking = record.get('web_source_tracking') or {}
    if tracking:
        counts = tracking.get('summary', {})
        lines += ['', '### 4.5 网页来源处理情况', '', '| 环节（按URL去重） | 数量 |', '| --- | --- |']
        for key, label in [('searched', '检索返回'), ('fetched', '取得文本'), ('fetch_failed', '抓取失败'),
                           ('not_attempted', '未抓取'), ('excluded', '原文初筛排除'),
                           ('eligible', '可供写作选择'), ('writing_selected', '已送入写作原文'), ('cited', '正文引用')]:
            lines.append(f'| {label} | {counts.get(key, 0)} |')
        lines += ['', tracking.get('note', ''), '', '每个链接的查询、处理状态及未采用原因见页面“网页来源处理明细”和关联JSON中的 web_source_tracking。', '']
    coverage = (record.get('report_quality') or {}).get('body_citation_coverage') or {}
    if coverage:
        lines += ['', '### 4.6 正文引用关联检查', '',
                  f"有引用关联的正文段落：{coverage.get('cited_paragraph_count', 0)}；"
                  f"缺少引用关联的重要事实段落或表格：{coverage.get('uncited_fact_paragraph_count', 0)}。", '',
                  '此项检查正文段落与来源是否关联，不以内部清单或图源代替正文引用，也不代表事实核验正确率。']
        lines += ['- ' + item['reason'] for item in coverage.get('issues', [])]
    lines += ["", "## 5 阶段耗时", "", "| 阶段 | 秒 |", "| --- | --- |"]
    for stage, duration in (record.get("stage_durations_seconds") or {}).items():
        lines.append(f"| {_cell(stage)} | {_cell(duration)} |")
    if not record.get("stage_durations_seconds"):
        lines.append("| 阶段记录 | 未记录 |")
    lines += ["", "## 6 验收轮次要求", "",
              "任务说明书要求至少10轮测试，且每轮均满足三项指标。本文件只对应上述运行编号；"
              "未复核的初筛结果不能计作通过的验收轮次。关联JSON保存本轮指标、核验明细及原始运行记录，供后续汇总复核。", ""]
    return "\n".join(lines)


async def export_evaluation_report(record, filename):
    from backend import utils

    assessment = assess_run(record)
    markdown = render_evaluation_report(record, assessment)
    # Keep the run suffix within the existing exporters' 60-character limit.
    stem = filename[:50] + "_指标测评"
    paths, errors = {}, []
    for kind, writer in (("md", utils.write_text_to_md), ("word", utils.write_md_to_word)):
        try:
            paths[kind + "_path"] = await writer(markdown, stem)
            if not paths[kind + "_path"]:
                errors.append(f"测评报告{kind}导出失败")
        except Exception as exc:
            paths[kind + "_path"] = ""
            errors.append(f"测评报告{kind}导出失败（{type(exc).__name__}）")
    json_path = Path("outputs") / (stem + ".json")
    paths["json_path"] = quote(json_path.as_posix())
    result = {**paths, "assessment": assessment, "errors": errors}
    try:
        await utils.write_to_file(str(json_path), json.dumps(
            {"assessment": assessment, "evaluation_report": result, "run_statistics": record},
            ensure_ascii=False, indent=2))
    except Exception as exc:
        result["json_path"] = ""
        errors.append(f"测评记录导出失败（{type(exc).__name__}）")
    return result
