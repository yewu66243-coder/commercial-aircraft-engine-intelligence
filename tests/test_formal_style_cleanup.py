"""Regression tests for deterministic formal-report style cleanup."""

import pytest
from time import perf_counter

from backend.reporting.finalization import clean_formal_report_style


DISCLAIMERS = [
    '受资料范围限制，本报告未取得 CCAR-33、FAR Part 33、CS-E 标准原文及适航指令原文，不对三大标准的条款协调性作结论。',
    '因此涉及具体适航指令编号、生效日期、检查改装要求及三大标准条款协调性的内容，本报告不作确定性结论。',
    '文献未将其与 FADEC 转速匹配或瞬态响应算法的验证建立关联，本报告不作此类推断。',
    '文献仅将电子控制器软件优化列为升级内容之一，未说明其发布形式、验证要求或是否触发运行包线重新验证，本报告不作此类定性。',
    '文献未说明认证缺失对运营范围的具体影响，本报告不作该因果推断。',
    '文献未涉及在役机队持续适航指令，本报告不对两类监管行为的性质作区分性判断。',
]


@pytest.mark.parametrize('sentence', DISCLAIMERS)
def test_removes_six_self_referential_disclaimers(sentence):
    cleaned, audit = clean_formal_report_style('# 结论\n\n' + sentence + '\n')
    assert sentence not in cleaned
    assert audit['version'] == 'formal-style-cleanup-v1'
    assert audit['removed_count'] == 1
    assert audit['removed_items'][0]['section'] == '结论'
    assert audit['removed_items'][0]['text'] == sentence
    assert audit['removed_items'][0]['rule']


@pytest.mark.parametrize('sentence', [
    '样本覆盖 2024—2026 年公开资料。',
    '本次共纳入 15 份可读取原文。',
    '公开资料未披露该部件价格。',
    '检索日期为 2026 年 9 月 28 日。',
    '原文仅覆盖三款发动机，未列出其他型号。',
])
def test_preserves_objective_scope_and_source_statements(sentence):
    report = '# 资料来源\n\n' + sentence + '\n'
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == report
    assert audit['removed_count'] == 0
    assert audit['removed_items'] == []


def test_keeps_valid_sentences_in_mixed_paragraph_and_removes_attached_citation():
    report = ('## 摘要\n\n样本覆盖 2024—2026 年公开资料。'
              + DISCLAIMERS[4] + '[URL1] 后续检索纳入 15 份原文。\n')
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == '## 摘要\n\n样本覆盖 2024—2026 年公开资料。后续检索纳入 15 份原文。\n'
    assert audit['removed_count'] == 1
    assert audit['removed_items'][0]['text'] == DISCLAIMERS[4] + '[URL1]'


def test_removes_orphan_citations_and_empty_paragraphs():
    report = ('# 讨论\n\n' + DISCLAIMERS[2] + '\n[原文1]\n\n'
              '保留事实。[URL2]\n')
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == '# 讨论\n\n保留事实。[URL2]\n'
    assert audit['removed_count'] == 1


def test_keeps_isolated_citation_after_retained_fact():
    report = ('# S\n' + DISCLAIMERS[4] + '\n\n'
              '保留事实。\n[原文 7]\n')
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == '# S\n\n保留事实。\n[原文 7]\n'
    assert audit['removed_count'] == 1


@pytest.mark.parametrize('boundary', [
    '## 下一节\n',
    '| 型号 | 信息 |\n| --- | --- |\n| A | 保留事实。 |\n',
])
def test_does_not_bind_orphan_citation_across_heading_or_table(boundary):
    report = '# S\n' + DISCLAIMERS[4] + '\n' + boundary + '[原文 7]\n'
    cleaned, audit = clean_formal_report_style(report)
    assert '[原文 7]\n' in cleaned
    assert audit['removed_count'] == 1


def test_removes_spaced_inline_citation_bound_to_deleted_sentence():
    report = '# 讨论\n\n' + DISCLAIMERS[2] + ' [原文1] 后续检索仍在进行。\n'
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == '# 讨论\n\n后续检索仍在进行。\n'
    assert audit['removed_items'][0]['text'] == DISCLAIMERS[2] + ' [原文1]'


def test_removes_retraction_clause_without_discarding_assertion():
    report = '# 结论\n\n软件升级已列入计划，因此无法证实其运行收益。\n'
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == '# 结论\n\n软件升级已列入计划。\n'
    assert audit['removed_count'] == 1
    assert audit['removed_items'][0]['text'] == '，因此无法证实其运行收益。'


def test_drops_connector_only_residue_and_emptied_table_rows():
    report = ('## 结果\n\n因此，' + DISCLAIMERS[4] + '\n\n'
              '| 型号 | 依据 | 结论 |\n| --- | --- | --- |\n'
              '| A | 公开资料未披露该部件价格。 | ' + DISCLAIMERS[4] + ' |\n'
              '| B | ' + DISCLAIMERS[4] + ' | [原文1] |\n')
    cleaned, audit = clean_formal_report_style(report)
    assert '因此，\n' not in cleaned
    assert '| A | 公开资料未披露该部件价格。 | — |' in cleaned
    assert '| B |' not in cleaned
    assert '| 型号 | 依据 | 结论 |' in cleaned
    assert '| --- | --- | --- |' in cleaned
    assert audit['removed_count'] == 3


@pytest.mark.parametrize('preserved_cell', [
    '图 1：样本覆盖 2024—2026 年公开资料。',
    '- 样本覆盖 2024—2026 年公开资料。',
    '**表2：** 样本覆盖 2024—2026 年公开资料。',
])
def test_preserves_untouched_prefixed_table_cell_when_another_cell_is_removed(preserved_cell):
    report = ('| 型号 | 图表说明 | 结论 |\n| --- | --- | --- |\n'
              '| A | ' + preserved_cell + ' | ' + DISCLAIMERS[4] + ' |\n')
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == ('| 型号 | 图表说明 | 结论 |\n| --- | --- | --- |\n'
                       '| A | ' + preserved_cell + ' | — |\n')
    assert audit['removed_count'] == 1


def test_preserves_escaped_pipes_in_untouched_table_cell():
    report = ('| 型号 | 依据 | 结论 |\n| --- | --- | --- |\n'
              '| A | ' + r'FADEC \| 控制 \| 调度' + ' | ' + DISCLAIMERS[4] + ' |\n')
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == ('| 型号 | 依据 | 结论 |\n| --- | --- | --- |\n'
                       '| A | ' + r'FADEC \| 控制 \| 调度' + ' | — |\n')
    assert audit['removed_count'] == 1


def test_even_number_of_backslashes_does_not_escape_table_delimiter():
    report = ('| 型号 | 依据一 | 依据二 | 结论 |\n| --- | --- | --- | --- |\n'
              '| A | ' + r'FADEC \\' + '| 控制 | ' + DISCLAIMERS[4] + ' |\n')
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == ('| 型号 | 依据一 | 依据二 | 结论 |\n| --- | --- | --- | --- |\n'
                       '| A | ' + r'FADEC \\' + '| 控制 | — |\n')
    assert audit['removed_count'] == 1


def test_heading_audit_tracks_nearest_section_across_body_and_caption():
    report = ('# 总报告\n\n## 摘要\n' + DISCLAIMERS[0] + '\n\n'
              '## 图表\n图 1：' + DISCLAIMERS[5] + '\n')
    cleaned, audit = clean_formal_report_style(report)
    assert DISCLAIMERS[0] not in cleaned
    assert DISCLAIMERS[5] not in cleaned
    assert [item['section'] for item in audit['removed_items']] == ['摘要', '图表']


@pytest.mark.parametrize('line', [
    '图 1：鉴于证据不足，不作结论。',
    '图 1. 鉴于证据不足，不作结论。',
    '**表2：** 鉴于证据不足，不作结论。',
    '- 鉴于证据不足，不作结论。',
    '* 鉴于证据不足，不作结论。',
    '+ 鉴于证据不足，不作结论。',
    '1. 鉴于证据不足，不作结论。',
])
def test_removes_scope_only_disclaimer_after_markdown_or_caption_prefix(line):
    cleaned, audit = clean_formal_report_style('## 图表\n' + line + '\n')
    assert cleaned == '## 图表\n'
    assert audit['removed_count'] == 1
    assert audit['removed_items'][0]['rule'] == 'scope_preface_with_conclusion'


def test_preserves_objective_caption_with_scope_preface():
    report = '## 图表\n图1：鉴于证据不足，但样本显示推力提高3%。\n'
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == report
    assert audit['removed_count'] == 0


def test_scope_preface_keeps_verified_fact_before_judgment_withdrawal():
    report = '# 结论\n鉴于证据不足，已核实其中30例事故，但不作进一步推断。\n'
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == report
    assert audit['removed_count'] == 0


def test_scope_preface_without_fact_is_removed():
    cleaned, audit = clean_formal_report_style('# 结论\n鉴于证据不足，不作进一步推断。\n')
    assert cleaned == '# 结论\n'
    assert audit['removed_count'] == 1


@pytest.mark.parametrize('sentence', [
    '本报告不对 EASA CS-E.510 作结论。',
    '本报告根据 FAR Part 33.4 不作定性判断。',
])
def test_dotted_technical_identifiers_do_not_split_sentence(sentence):
    cleaned, audit = clean_formal_report_style('# 结论\n' + sentence + '\n')
    assert cleaned == '# 结论\n'
    assert audit['removed_count'] == 1
    assert audit['removed_items'][0]['text'] == sentence


def test_ascii_period_still_ends_sentence():
    cleaned, audit = clean_formal_report_style('# S\n本报告不作结论.保留事实.\n')
    assert cleaned == '# S\n保留事实.\n'
    assert audit['removed_count'] == 1


@pytest.mark.parametrize('protected', [
    '[本报告不作结论。](https://example.com/guide)',
    '![本报告不作结论。](https://example.com/chart.png)',
    '`本报告不作结论。`',
    '<https://example.com/本报告不作结论。>',
])
def test_markdown_inline_constructs_remain_byte_exact(protected):
    report = '# S\n参见' + protected + '了解规范。\n'
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == report
    assert audit['removed_count'] == 0


@pytest.mark.parametrize('fence', ['```', '~~~'])
def test_fenced_code_is_not_edited_and_stops_orphan_binding(fence):
    report = ('# S\n文献未说明认证缺失对运营范围的具体影响，本报告不作该因果推断。\n'
              + fence + 'python\n本报告不作结论。\n' + fence + '\n[URL1]\n')
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == '# S\n' + fence + 'python\n本报告不作结论。\n' + fence + '\n[URL1]\n'
    assert audit['removed_count'] == 1


@pytest.mark.parametrize('reference', ['[文献1]', '[来源1]', '[1]', '[原文 7]', '[URL1]'])
def test_orphan_citation_after_blank_lines_is_removed(reference):
    report = '# S\n' + DISCLAIMERS[4] + '\n\n\n' + reference + '\n保留事实。\n'
    cleaned, audit = clean_formal_report_style(report)
    assert reference not in cleaned
    assert '保留事实。' in cleaned
    assert audit['removed_count'] == 1


def test_citation_after_retained_fact_and_blank_line_remains():
    report = '# S\n' + DISCLAIMERS[4] + '\n\n保留事实。\n\n[文献1]\n'
    cleaned, _ = clean_formal_report_style(report)
    assert '保留事实。\n\n[文献1]' in cleaned


def test_long_unpunctuated_line_is_linear_and_not_removed():
    report = '# S\n' + '本报告' * 6667 + '\n'
    started = perf_counter()
    cleaned, audit = clean_formal_report_style(report)
    elapsed = perf_counter() - started
    assert cleaned == report
    assert audit['removed_count'] == 0
    assert elapsed < 2.0


def test_cleanup_is_idempotent():
    report = '# 摘要\n\n' + DISCLAIMERS[3] + '\n\n公开资料未披露该部件价格。\n'
    first, _ = clean_formal_report_style(report)
    second, audit = clean_formal_report_style(first)
    assert second == first
    assert audit['removed_count'] == 0


def test_no_match_preserves_markdown_byte_for_byte():
    report = '# 标题\n\n样本覆盖 2024—2026 年公开资料。  [原文1]\n\n| A | B |\n| --- | --- |\n| x | y |\n'
    cleaned, audit = clean_formal_report_style(report)
    assert cleaned == report
    assert audit == {'version': 'formal-style-cleanup-v1', 'removed_count': 0, 'removed_items': []}
