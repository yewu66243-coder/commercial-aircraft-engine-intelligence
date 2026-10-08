"""Tone cleanup for formal Chinese research reports."""
from __future__ import annotations

import re


HARSH_SOURCE_MARKERS = (
    "所查资料",
    "所引资料",
    "原文未明确",
    "需核验",
    "推断错误",
)


def _protect_references(text: str) -> tuple[str, list[str]]:
    refs: list[str] = []

    def repl(match: re.Match[str]) -> str:
        refs.append(match.group(0))
        return f"@@REF{len(refs) - 1}@@"

    return re.sub(r"\[(?:原文|URL|来源URL)?\d+\]|\[[A-Z]+\d+\]|\[\d+\]", repl, text), refs


def _restore_references(text: str, refs: list[str]) -> str:
    for index, ref in enumerate(refs):
        text = text.replace(f"@@REF{index}@@", ref)
    return text


def soften_source_boundary_tone(text: str) -> str:
    """Convert disclaimer-like source caveats into formal scope language.

    The function is intentionally conservative: it does not invent evidence,
    delete numbers, alter citations, or turn an uncertain claim into a fact.
    It only rewrites recurring report-generation phrasing that reads like a
    disclaimer rather than an analytic boundary statement.
    """
    if not text:
        return text
    protected, refs = _protect_references(text)
    output = protected

    replacements = [
        (
            r"该问题涉及多个机型、数百架飞机，但(?:本轮资料|所查资料|所引资料)未将其表述为适航指令或强制措施，报告在讨论时区分OEM改进计划与监管强制要求。",
            "该问题涉及多个机型、数百架飞机，本文将其作为大规模适航风险和维修组织问题分析，并把OEM改进计划与监管强制要求分开表述。",
        ),
        (
            r"(?:本轮资料|所查资料|所引资料)描述的是型号合格证颁发和OEM技术改进，未提供针对([^，。；;]+)的AD编号或具体合规时限。",
            r"相关材料指向型号取证和OEM技术改进，尚不能直接归入针对\1的AD合规安排。",
        ),
        (
            r"该工艺的批准状态和适用范围在(?:本轮资料|所查资料|所引资料)中未进一步说明。",
            "本文将该工艺作为维修能力提升举措处理，适用边界仍需后续从批准文件或服务文件中确认。",
        ),
        (
            r"原文未明确[“\"]年底[”\"]所指年份，需结合资料发表时间核验。",
            "这里的“年底”应按资料发表背景理解，后续核对时需要回到原刊期和后续公告。",
        ),
        (
            r"上述循环数为TAU数据推算，与括号内([^，。；;]+)的口径差异需核验。",
            r"这些循环数来自TAU口径推算，和括号中\1的机队数量统计并非同一口径，报告只用其说明暴露规模。",
        ),
        (
            r"上述审定状态截至([^，。；;]+)，后续进展在(?:本轮资料|所查资料|所引资料)中未更新。",
            r"因此，本文按\1时点表述其审定进度，后续状态需结合监管数据库和企业公告补充。",
        ),
        (
            r"该发动机尚未投入商业运营，(?:本轮资料|所查资料|所引资料)未提供针对其发布的适航指令信息。",
            "由于该发动机尚未投入商业运营，本文仅将其列为新取证型号和技术储备案例，不纳入在役机队强制措施比较。",
        ),
        (
            r"(?:本轮资料|所查资料|所引资料)未提供该发动机系列在监测期内有强制适航措施的信息。",
            "资料中的信息主要指向取证和交付进展，本文不把该发动机系列列为监测期内已有强制适航措施的对象。",
        ),
        (
            r"将OEM计划等同于适航指令要求，是监测工作中需要避免的推断错误。",
            "监测时需要把产品改进计划与AD合规窗口分开，避免把两类时间节点混用。",
        ),
        (
            r"(?:本轮资料|所引资料|所查资料)未显示监管机构设定了固定合规窗口，也未说明未按期完成的后果。",
            "这些时间节点并未构成统一的监管合规窗口，报告因此不推导逾期后果。",
        ),
        (
            r"(?:本轮资料|所查资料|所引资料)未说明具体的运营限制要求。",
            "资料重点描述相关技术或维修安排，未展开运营人必须采取的具体限制条款。",
        ),
        (
            r"(?:本轮资料|所查资料|所引资料)未说明改装完成前的具体运营限制。",
            "资料重点描述改装目标，未展开改装完成前的具体运行限制条款。",
        ),
        (
            r"此外，(?:本轮资料|所查资料|所引资料)未提供CAAC针对上述发动机型号发布的适航指令信息。这一缺口不意味着CAAC未采取行动，仅表明(?:本轮资料|所查资料|所引资料)未覆盖。",
            "此外，CAAC相关信息仍需在监管数据库中单独补查。本轮材料没有覆盖，不据此判断监管是否采取行动。",
        ),
    ]
    for pattern, replacement in replacements:
        output = re.sub(pattern, replacement, output)

    generic_replacements = [
        (r"在所查资料范围内", "基于本轮资料"),
        (r"在所引资料范围内", "基于本轮资料"),
        (r"从所查资料看", "从现有资料看"),
        (r"从所引资料看", "从现有资料看"),
        (r"所查资料中", "本轮材料中"),
        (r"所引资料中", "本轮材料中"),
        (r"所查资料", "本轮资料"),
        (r"所引资料", "本轮资料"),
        (r"未进一步说明", "尚需进一步确认"),
        (r"原文未明确", "原文尚未明确"),
        (r"需结合资料发表时间核验", "应结合资料发表时间理解"),
        (r"需核验", "需后续确认"),
    ]
    for old, new in generic_replacements:
        output = re.sub(old, new, output)

    # Prefer compact formal boundaries over repeated “not provided” clauses.
    output = re.sub(
        r"本轮资料未提供([^。；;]{1,80}?)(?:的信息|的资料)?。",
        r"\1仍需后续补查。",
        output,
    )
    output = re.sub(
        r"本轮资料未显示([^。；;]{1,80}?)(?:的信息|的资料)?。",
        r"\1仍需后续补查。",
        output,
    )
    output = re.sub(r"本轮资料范围内", "本轮资料中", output)
    output = re.sub(r"资料边界是本轮资料", "资料边界是本轮材料", output)
    output = re.sub(r"报告因此不推导", "报告不推导", output)
    return _restore_references(output, refs)


def tone_warning_count(text: str) -> int:
    return sum(text.count(marker) for marker in HARSH_SOURCE_MARKERS)
