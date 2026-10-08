"""User-configurable report layout and chapter profile helpers."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Dict, Iterable, List


DEFAULT_REPORT_FORMAT_PROFILE: Dict[str, Any] = {
    "font": {
        "body": "SimSun",
        "heading": "SimHei",
        "reference": "SimSun",
    },
    "size": {
        "body": 12.0,
        "heading1": 15.0,
        "heading2": 13.0,
        "heading3": 12.0,
        "caption": 10.5,
        "reference": 12.0,
    },
    "layout": {
        "page": "A4",
        "margin_mm": 25.0,
        "line_spacing": 1.5,
        "include_toc": True,
        "cover": True,
        "numbering": True,
    },
    "chapters": [],
}


_FONT_ALIASES = {
    "宋体": "SimSun",
    "黑体": "SimHei",
    "微软雅黑": "Microsoft YaHei",
    "楷体": "KaiTi",
    "仿宋": "FangSong",
    "times new roman": "Times New Roman",
    "simsun": "SimSun",
    "simhei": "SimHei",
    "microsoft yahei": "Microsoft YaHei",
    "kaiti": "KaiTi",
    "fangsong": "FangSong",
}


def _clean_font(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    value = re.sub(r"[\r\n\t;{}<>]", "", value).strip()
    if not value:
        return fallback
    return _FONT_ALIASES.get(value.lower(), _FONT_ALIASES.get(value, value[:40]))


def _number(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        return fallback
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if not minimum <= numeric <= maximum:
        return fallback
    return round(numeric, 2)


def _bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return fallback


def _clean_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"[#|<>{}\[\]\r\n\t]", "", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value[:40]


def normalize_chapters(chapters: Any) -> List[Dict[str, str]]:
    """Return sanitized custom chapters; empty means use system framework."""
    if isinstance(chapters, str):
        raw_items: Iterable[Any] = [line for line in chapters.splitlines() if line.strip()]
    elif isinstance(chapters, list):
        raw_items = chapters
    else:
        return []
    result: List[Dict[str, str]] = []
    seen = set()
    for item in raw_items:
        if isinstance(item, dict):
            title = _clean_title(item.get("title"))
            instruction = _clean_title(item.get("instruction") or item.get("question") or "")
        else:
            title = _clean_title(item)
            instruction = ""
        if not title or title in seen:
            continue
        seen.add(title)
        result.append({"title": title, "instruction": instruction})
        if len(result) >= 12:
            break
    return result


def normalize_report_format_profile(value: Any) -> Dict[str, Any]:
    """Validate a user profile and fill defaults for omitted fields."""
    profile = deepcopy(DEFAULT_REPORT_FORMAT_PROFILE)
    if not isinstance(value, dict):
        return profile
    font = value.get("font") if isinstance(value.get("font"), dict) else {}
    size = value.get("size") if isinstance(value.get("size"), dict) else {}
    layout = value.get("layout") if isinstance(value.get("layout"), dict) else {}
    profile["font"]["body"] = _clean_font(font.get("body"), profile["font"]["body"])
    profile["font"]["heading"] = _clean_font(font.get("heading"), profile["font"]["heading"])
    profile["font"]["reference"] = _clean_font(font.get("reference"), profile["font"]["reference"])
    profile["size"]["body"] = _number(size.get("body"), profile["size"]["body"], 9.0, 16.0)
    profile["size"]["heading1"] = _number(size.get("heading1"), profile["size"]["heading1"], 11.0, 24.0)
    profile["size"]["heading2"] = _number(size.get("heading2"), profile["size"]["heading2"], 10.0, 20.0)
    profile["size"]["heading3"] = _number(size.get("heading3"), profile["size"]["heading3"], 9.0, 18.0)
    profile["size"]["caption"] = _number(size.get("caption"), profile["size"]["caption"], 8.0, 12.0)
    profile["size"]["reference"] = _number(size.get("reference"), profile["size"]["reference"], 8.0, 14.0)
    page = str(layout.get("page") or profile["layout"]["page"]).upper()
    profile["layout"]["page"] = "Letter" if page in {"LETTER", "US LETTER"} else "A4"
    profile["layout"]["margin_mm"] = _number(layout.get("margin_mm"), profile["layout"]["margin_mm"], 12.0, 35.0)
    profile["layout"]["line_spacing"] = _number(layout.get("line_spacing"), profile["layout"]["line_spacing"], 1.0, 2.0)
    profile["layout"]["include_toc"] = _bool(layout.get("include_toc"), profile["layout"]["include_toc"])
    profile["layout"]["cover"] = _bool(layout.get("cover"), profile["layout"]["cover"])
    profile["layout"]["numbering"] = _bool(layout.get("numbering"), profile["layout"]["numbering"])
    profile["chapters"] = normalize_chapters(value.get("chapters"))
    return profile


def custom_chapters_to_framework(chapters: List[Dict[str, str]]) -> List[Dict[str, str]]:
    result = []
    for item in chapters:
        title = item.get("title", "")
        instruction = item.get("instruction", "")
        result.append({
            "title": title,
            "question": instruction or f"围绕“{title}”回答本任务中的对应问题。",
            "evidence": "与本章主题最直接相关的本地资料、网页证据、论文或专利。",
            "logic": "按用户指定章节展开；先给判断，再给证据和对任务的含义。",
        })
    return result
