"""Plan task-specific public report structures before writing."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List

from .format_profile import custom_chapters_to_framework


def _chapter(title: str, question: str, evidence: str, logic: str) -> Dict[str, str]:
    return {"title": title, "question": question, "evidence": evidence, "logic": logic}


_FRAMEWORKS: Dict[str, Dict[str, object]] = {
    "airworthiness_maintenance_monitoring": {
        "name": "适航与维修措施监测框架",
        "keywords": ("适航", "适航指令", "AD", "服务通告", "SB", "ASB", "强制维修", "合规", "审定", "MRO", "维修"),
        "chapters": [
            _chapter("问题背景与监测对象", "本次监测针对哪些发动机、机队或维修事件。", "型号、时间、机构、事件触发点。", "先限定对象，再说明为什么需要监测。"),
            _chapter("技术风险与受影响部件", "风险来自部件、材料、制造或使用环节中的哪一类问题。", "部件名称、失效模式、批次、寿命或维修周期。", "把技术原因和后续适航措施连接起来。"),
            _chapter("适航响应与服务文件进展", "监管机构和OEM分别采取了什么措施，二者边界如何。", "AD、NPRM、SB、ASB、服务计划、审定信息。", "区分强制监管要求、OEM服务建议和一般技术改进。"),
            _chapter("维修执行与运营影响", "维修、停场、备发、供应链或成本影响体现在哪里。", "停场数量、维修周期、MRO能力、交付与航班影响。", "说明技术事件如何转化为运行和保障约束。"),
            _chapter("综合研判与后续监测重点", "哪些结论已经较稳，哪些需要继续跟踪。", "跨来源一致点、口径差异、待补查数据库。", "集中处理资料边界，不在每段后追加免责声明。"),
            _chapter("结论与建议", "对用户任务给出可执行的结论和后续动作。", "前文已有证据。", "逐项回应任务，不引入新事实。"),
        ],
    },
    "patent_landscape": {
        "name": "专利布局分析框架",
        "keywords": ("专利", "申请人", "发明人", "权利要求", "专利族", "布局", "IPC", "CPC"),
        "chapters": [
            _chapter("研究对象与技术范围", "本次专利分析覆盖哪类技术问题。", "关键词、技术方向、代表性对象。", "先界定范围，避免把无关专利纳入判断。"),
            _chapter("专利总体态势", "申请数量、时间变化和地域布局呈现什么特征。", "申请日、公开日、国家地区、法律状态。", "用趋势服务于技术竞争判断。"),
            _chapter("核心申请人与技术方向", "主要申请人分别押注哪些路线。", "申请人、专利族、技术关键词。", "比较不同主体的布局重点。"),
            _chapter("关键专利族与保护重点", "哪些专利族可能构成技术或自由实施风险。", "权利要求、同族、引用、适用部件。", "从权利要求保护范围回到工程影响。"),
            _chapter("研发启示与风险建议", "后续研发和规避设计应关注什么。", "前述专利证据。", "提出可执行建议。"),
        ],
    },
    "technical_route_research": {
        "name": "技术路线研究框架",
        "keywords": ("技术路线", "机理", "构型", "冷却", "燃烧", "材料", "寿命预测", "控制", "FADEC", "性能", "故障"),
        "chapters": [
            _chapter("研究背景与问题界定", "技术问题为什么出现，研究对象是什么。", "型号、部件、工况、性能指标。", "把任务问题转成可分析的工程问题。"),
            _chapter("关键机理与约束条件", "影响结果的核心机制和约束是什么。", "材料、结构、载荷、温度、寿命或控制参数。", "先解释原因，再讨论方案。"),
            _chapter("国内外进展与代表性方案", "现有方案分别解决了什么问题。", "论文、专利、标准、企业资料。", "用同口径比较代替资料罗列。"),
            _chapter("工程化难点与适航影响", "从研究到工程应用还受哪些限制。", "制造、试验、审定、可靠性、维修性证据。", "说明技术可行性和应用边界。"),
            _chapter("趋势判断与建议", "该技术后续可能如何演进。", "前述证据和约束。", "形成对课题有用的判断。"),
        ],
    },
    "market_supply_chain": {
        "name": "市场与供应链影响框架",
        "keywords": ("市场", "供应链", "交付", "运营", "停场", "产能", "售后", "成本", "航司", "订单", "MRO"),
        "chapters": [
            _chapter("事件背景与影响链条", "事件从技术或监管问题如何传导到市场。", "时间线、主体、触发因素。", "建立技术、维修、运营和市场之间的因果链。"),
            _chapter("受影响主体与规模", "哪些企业、机队或客户受到影响。", "机型、发动机型号、数量、区域、客户。", "统一统计口径后再比较。"),
            _chapter("维修产能与供应链约束", "瓶颈集中在产能、备件还是服务网络。", "MRO能力、备发、交付节奏、召回计划。", "说明约束如何影响恢复速度。"),
            _chapter("运营成本与竞争影响", "事件对航司、OEM和售后市场有什么影响。", "停场、成本、赔付、交付延迟、市场份额。", "从证据推导影响，不做无来源外推。"),
            _chapter("后续走势与应对建议", "哪些变量决定后续变化。", "公开计划、监管进展、产能数据。", "给出跟踪指标和行动建议。"),
        ],
    },
    "model_tracking": {
        "name": "型号动态跟踪框架",
        "keywords": ("型号", "A320neo", "737", "LEAP", "GTF", "PW", "遄达", "CJ1000", "CJ2000", "跟踪", "商业运营"),
        "chapters": [
            _chapter("型号与任务背景", "本次跟踪对象和任务边界是什么。", "型号、平台、运营状态。", "限定范围，避免泛化到全部发动机。"),
            _chapter("关键技术与构型变化", "型号的关键技术变化体现在哪里。", "构型、部件、性能、控制系统。", "把技术变化和后续影响连接。"),
            _chapter("适航审定与服务文件", "审定、AD、SB或服务文件有哪些进展。", "监管记录、OEM文件、服务公告。", "区分已发布要求与一般计划。"),
            _chapter("运营表现与维修保障", "运营或维修保障层面有哪些信号。", "机队、停场、维修周期、可靠性。", "从运行数据解释保障压力。"),
            _chapter("后续跟踪重点", "下一步应继续观察哪些指标。", "前述证据缺口和变化变量。", "集中说明边界与监测动作。"),
        ],
    },
}


def _score(text: str, keywords: Iterable[str]) -> int:
    score = 0
    for keyword in keywords:
        if not keyword:
            continue
        pattern = re.escape(keyword)
        hits = len(re.findall(pattern, text, flags=re.I))
        score += hits * (3 if len(keyword) >= 3 else 1)
    return score


def build_report_framework_plan(*, task: str, report_type: str = "",
                                demand_text: str = "", source_template_text: str = "",
                                custom_chapters: List[Dict[str, str]] | None = None) -> Dict[str, object]:
    """Choose a public report framework from task semantics and known routing context."""
    if custom_chapters:
        return {
            "id": "custom_user_framework",
            "name": "用户自定义报告框架",
            "match_score": None,
            "public_method_section": False,
            "internal_method_note": "用户已指定公开正文章节；资料来源、检索范围和证据缺口仍进入后台研究记录。",
            "chapters": custom_chapters_to_framework(custom_chapters),
            "custom": True,
        }
    text = "\n".join([task or "", report_type or "", demand_text or "", source_template_text or ""])
    scored: List[tuple[int, str]] = [
        (_score(text, profile["keywords"]), framework_id)
        for framework_id, profile in _FRAMEWORKS.items()
    ]
    scored.sort(reverse=True)
    best_score, best_id = scored[0]
    if best_score <= 0:
        best_id = "technical_route_research" if report_type == "resource_report" else "model_tracking"
    profile = _FRAMEWORKS[best_id]
    return {
        "id": best_id,
        "name": profile["name"],
        "match_score": best_score,
        "public_method_section": False,
        "internal_method_note": "资料来源、检索范围、源站权重和证据缺口进入后台研究记录；公开正文只在必要处简短说明资料边界。",
        "chapters": profile["chapters"],
    }


def format_framework_for_prompt(plan: Dict[str, object]) -> str:
    chapters = plan.get("chapters") or []
    lines = [
        f"框架类型：{plan.get('name') or plan.get('id') or '任务自适应研究框架'}。",
        "公开正文默认不设置“资料来源与研究方法”独立章节；必要的方法和边界说明合并到引言、综合研判或内部核验记录。",
        "章节应按问题链条推进，不能把材料来源逐条堆砌成事实清单。",
        "每个章节都应有明确的分析功能：提出本章判断、给出证据、解释其对全文结论的作用。",
        "成稿前按反向提纲自查，确保章节首句连起来能够构成完整论证路线。",
    ]
    for index, chapter in enumerate(chapters, 1):
        if not isinstance(chapter, dict):
            continue
        lines.append(
            f"{index}. {chapter.get('title')}: 回答“{chapter.get('question')}”；"
            f"优先使用{chapter.get('evidence')}；写作逻辑为{chapter.get('logic')}"
        )
    return "\n".join(lines)
