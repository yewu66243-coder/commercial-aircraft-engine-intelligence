from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


TEMPLATE_FILE_NAMES = {
    "demand_models": "demand_models.json",
    "task_templates": "task_templates.json",
    "source_templates": "source_templates.json",
}


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_local_docs_root() -> Path:
    return Path(os.getenv("LOCAL_DOCS_PATH", get_project_root() / "local_docs")).resolve()


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    return re.sub(r"\s+", " ", text).strip()


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def load_intelligence_templates() -> dict[str, list[dict[str, Any]]]:
    root = get_local_docs_root()
    return {
        key: _read_json_list(root / file_name)
        for key, file_name in TEMPLATE_FILE_NAMES.items()
    }


def get_template_catalog() -> dict[str, Any]:
    catalog = load_intelligence_templates()
    return {
        **catalog,
        "template_root": str(get_local_docs_root()),
        "files": {
            key: str(get_local_docs_root() / file_name)
            for key, file_name in TEMPLATE_FILE_NAMES.items()
        },
    }


def _keyword_score(text: str, keywords: list[Any], weight: float = 1.0) -> float:
    score = 0.0
    for keyword in keywords or []:
        token = _normalize_text(keyword)
        if token and token in text:
            score += weight + min(len(token), 20) * 0.04
    return score


def infer_demand_profile(task: str, explicit_model_id: str | None = None) -> dict[str, Any]:
    models = [item for item in load_intelligence_templates()["demand_models"] if item.get("enabled", True)]
    task_text = _normalize_text(task)
    if not models:
        return {"id": "", "name": "未配置领域需求模型", "matched_topics": [], "score": 0.0}

    best_model: dict[str, Any] | None = None
    best_score = -1.0
    for model in models:
        if explicit_model_id and model.get("id") == explicit_model_id:
            best_model = model
            best_score = 999.0
            break

        score = _keyword_score(task_text, model.get("keywords", []), 2.0)
        for obj in model.get("core_objects", []):
            if not isinstance(obj, dict):
                continue
            score += _keyword_score(task_text, [obj.get("name")], 1.6)
            score += _keyword_score(task_text, obj.get("aliases", []), 1.8)
        for topic in model.get("topics", []):
            if isinstance(topic, dict):
                score += _keyword_score(task_text, topic.get("keywords", []), 1.4)
        if score > best_score:
            best_model = model
            best_score = score

    model = dict(best_model or models[0])
    matched_topics: list[dict[str, Any]] = []
    for topic in model.get("topics", []):
        if not isinstance(topic, dict):
            continue
        topic_score = _keyword_score(task_text, topic.get("keywords", []), 1.0)
        if topic_score > 0:
            topic_copy = dict(topic)
            topic_copy["match_score"] = round(topic_score, 3)
            matched_topics.append(topic_copy)
    if not matched_topics:
        matched_topics = [dict(topic) for topic in model.get("topics", [])[:3] if isinstance(topic, dict)]
        for topic in matched_topics:
            topic["match_score"] = 0.0

    model["score"] = round(best_score, 3)
    model["matched_topics"] = sorted(
        matched_topics,
        key=lambda item: (float(item.get("match_score") or 0), len(item.get("keywords") or [])),
        reverse=True,
    )[:4]
    return model


def build_demand_query(task: str, profile: dict[str, Any]) -> str:
    object_terms: list[str] = []
    for obj in profile.get("core_objects", []):
        if not isinstance(obj, dict):
            continue
        object_terms.extend([obj.get("name", "")])
        object_terms.extend(obj.get("aliases", [])[:4])

    topic_terms: list[str] = []
    for topic in profile.get("matched_topics", []):
        if not isinstance(topic, dict):
            continue
        topic_terms.extend(topic.get("keywords", [])[:8])

    required_entities: list[str] = []
    for topic in profile.get("matched_topics", []):
        if isinstance(topic, dict):
            required_entities.extend(topic.get("required_entities", [])[:4])

    parts = [task.strip()]
    if profile.get("name"):
        parts.append(f"领域需求模型：{profile['name']}")
    if object_terms:
        parts.append("核心关注对象：" + "、".join(_unique(object_terms)[:18]))
    if topic_terms:
        parts.append("情报主题关键词：" + "、".join(_unique(topic_terms)[:24]))
    if required_entities:
        parts.append("应重点抽取实体：" + "、".join(_unique(required_entities)[:18]))
    return "。".join(part for part in parts if part)


def build_planner_subtopics(task: str, profile: dict[str, Any], domains: list[str], max_topics: int = 3) -> list[str]:
    max_topics = max(1, min(int(max_topics), 6))
    domain_hint = f"。优先关注这些站点：{', '.join(domains)}" if domains else ""
    topics = [topic for topic in profile.get("matched_topics", []) if isinstance(topic, dict)]
    subtopics: list[str] = []
    for topic in topics[:max_topics]:
        questions = "；".join(topic.get("priority_questions", [])[:3])
        required = "、".join(topic.get("required_entities", [])[:6])
        subtopics.append(
            f"{task}。围绕“{topic.get('name', '情报主题')}”开展检索与研判；"
            f"重点问题：{questions or '提取关键事实、参数与影响'}；"
            f"重点实体：{required or '型号、机构、时间、参数、证据来源'}{domain_hint}"
        )

    fallback_topics = [
        "官方口径、适航指令、公告、认证标准或监管动态",
        "底层技术进展、核心物理参数、控制算法模型、测试验证状态或维护保障规范",
        "交付数量、客户应用案例、市场占有率、产业合作和商业影响",
        "事件时间线、不同型号或方案的同口径比较及相互矛盾的来源",
        "典型应用或运营案例中的措施、约束、实施结果及适用条件",
        "现有措施的局限、尚未解决的问题与需要持续跟踪的指标",
    ]
    for topic in fallback_topics:
        if len(subtopics) >= max_topics:
            break
        subtopics.append(f"{task}。请重点梳理{topic}{domain_hint}")
    return subtopics[:max_topics]


def recommend_source_templates(
    task: str,
    profile: dict[str, Any],
    selected_source_ids: list[str] | None = None,
    selected_categories: list[str] | None = None,
) -> list[dict[str, Any]]:
    sources = [item for item in load_intelligence_templates()["source_templates"] if item.get("domain")]
    task_text = _normalize_text(task)
    topic_ids = {
        str(topic.get("id"))
        for topic in profile.get("matched_topics", [])
        if isinstance(topic, dict) and topic.get("id")
    }
    explicit_ids = {str(item) for item in selected_source_ids or [] if str(item).strip()}
    categories = {str(item) for item in selected_categories or [] if str(item).strip()}
    if selected_source_ids is not None and not explicit_ids and not categories:
        return []

    ranked: list[dict[str, Any]] = []
    for source in sources:
        if explicit_ids and str(source.get("id")) not in explicit_ids:
            continue
        if categories and str(source.get("category")) not in categories:
            continue
        score = float(source.get("weight") or 0.5) * 60 + float(source.get("credibility") or 0.5) * 40
        source_topics = {str(item) for item in source.get("topics", [])}
        if topic_ids & source_topics:
            score += 25
        score += _keyword_score(task_text, source.get("keywords", []), 4.0)
        if source.get("default_enabled"):
            score += 8
        item = dict(source)
        item["selection_score"] = round(score, 3)
        ranked.append(item)

    return sorted(ranked, key=lambda item: item.get("selection_score", 0), reverse=True)


def merge_weighted_domains(
    manual_domains: list[str] | None,
    recommended_sources: list[dict[str, Any]],
    limit: int = 12,
) -> list[str]:
    template_domains = [str(item.get("domain") or "").strip() for item in recommended_sources]
    manual = [str(item or "").strip() for item in manual_domains or []]
    return _unique(template_domains + manual)[: max(1, limit)]
