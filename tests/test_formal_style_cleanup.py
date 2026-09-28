"""Regression tests for deterministic formal-report style cleanup."""

import pytest

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


def test_heading_audit_tracks_nearest_section_across_body_and_caption():
    report = ('# 总报告\n\n## 摘要\n' + DISCLAIMERS[0] + '\n\n'
              '## 图表\n图 1：' + DISCLAIMERS[5] + '\n')
    cleaned, audit = clean_formal_report_style(report)
    assert DISCLAIMERS[0] not in cleaned
    assert DISCLAIMERS[5] not in cleaned
    assert [item['section'] for item in audit['removed_items']] == ['摘要', '图表']


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
