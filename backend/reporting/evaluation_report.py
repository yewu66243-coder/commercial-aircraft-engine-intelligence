"""Render the stable evaluation summary as a standalone, offline report.

Only presentation fields from ``summary`` enter the document. Raw evaluator
results are accepted for the caller's existing interface but never serialized.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from backend.reporting.document_export import render_pdf, render_word


_PUBLIC_DIR = "/outputs/evaluations"
_REASONS = {
    "supported": "来源充分支撑该断言。",
    "partially_supported": "来源仅部分支撑该断言。",
    "unsupported": "未找到充分来源支撑。",
    "unchecked": "该断言尚未完成核验。",
}
_STATE = {
    "completed": "已完成",
    "proxy": "代理评估",
    "invalid_ground_truth": "标准答案无效，未执行严格实体评估。",
    "evaluation_failed": "测评失败，结果未完成。",
    "no_public_urls": "无公开URL，无法计算可访问率。",
    "no_public_relationships": "无公开断言—URL关系，无法计算支撑准确率。",
    "not_evaluated": "未测评。",
}
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                     *(f"LPT{i}" for i in range(1, 10))}


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _text(value: object, limit: int = 500) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return "—"
    if isinstance(value, float) and not math.isfinite(value):
        return "—"
    result = str(value)
    result = re.sub(r"[\x00-\x1f\x7f]+", " ", result)
    result = re.sub(r"(?i)(?:[A-Z]:[\\/](?!/)|\\\\)[^\s|]+", "[路径已隐藏]", result)
    result = re.sub(r"(?i)(?<!\w)/(?:Users|home|tmp|var|private|mnt|etc)/[^\s|]+",
                    "[路径已隐藏]", result)
    result = re.sub(r"(?i)\b(?:api[_-]?key|authorization|bearer|token|secret)\s*[:=]\s*\S+",
                    "[凭据已隐藏]", result)
    result = result.strip()
    return (result[:limit] + "…") if len(result) > limit else result or "—"


def _cell(value: object, limit: int = 500) -> str:
    return _text(value, limit).replace("\\", "\\\\").replace("|", "\\|")


def _rate(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    numeric = float(value)
    return f"{numeric * 100:.2f}".rstrip("0").rstrip(".") + "%" if math.isfinite(numeric) and 0 <= numeric <= 1 else "—"


def _count(value: object) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else "—"


def _status(value: object) -> str:
    return _STATE.get(value, "未测评。") if isinstance(value, str) else "未测评。"


def _verdict(value: object) -> str:
    return "达标" if value is True else "未达标" if value is False else "—"


def _url(value: object) -> str:
    if not isinstance(value, str):
        return "—"
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return "—"
        # Query strings can carry keys or user data; never publish them.
        host = parsed.hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
        netloc = f"[{host}]" if ":" in host else host
        return _text(urlunsplit((parsed.scheme.lower(), netloc + port, parsed.path, "", "")), 1000)
    except (ValueError, TypeError):
        return "—"


def _table(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(item, 1000 if i == 0 and "URL" in headers[0] else 500)
                                 for i, item in enumerate(row)) + " |" for row in rows)
    return lines


def _entity_name(value: object) -> str:
    if isinstance(value, str):
        return _text(value)
    item = _mapping(value)
    if "entity" in item or "name" in item:
        return _text(item.get("entity") or item.get("name"))
    predicted, expected = _mapping(item.get("predicted")), _mapping(item.get("expected"))
    return _text(predicted.get("name") or predicted.get("value") or expected.get("name")
                 or expected.get("value"))


def render_evaluation_report_markdown(*, task: str, run_id: str, evaluated_at: str,
                                      summary: dict, entity_eval: dict, url_check: dict,
                                      url_source_eval: dict) -> str:
    """Build a deterministic Chinese report without model calls or report prose."""
    del entity_eval, url_check, url_source_eval
    safe = _mapping(summary)
    entity = _mapping(safe.get("entity"))
    links = _mapping(safe.get("public_links"))
    access = _mapping(links.get("accessibility"))
    support = _mapping(links.get("claim_support"))
    cleanup = _mapping(safe.get("style_cleanup"))
    ground_truth = _text(entity.get("ground_truth_path"), 120)
    if ground_truth != "—":
        ground_truth = _text(ground_truth.replace("\\", "/").rsplit("/", 1)[-1], 120)

    lines = [
        "# 独立测评报告", "", "| 字段 | 内容 |", "| --- | --- |",
        f"| 任务名称 | {_cell(task, 200)} |",
        f"| 测评时间 | {_cell(evaluated_at, 80)} |",
        f"| 报告标识 | {_cell(run_id, 120)} |",
        f"| 标准答案安全文件名 | {_cell(ground_truth, 120)} |",
        f"| 总体状态 | {_cell({'completed': '已完成', 'partial': '部分完成', 'failed': '测评失败'}.get(safe.get('status'), '未测评'))} |",
        "", "## 指标定义与阈值", "",
        "- 实体：精确率 P = TP / (TP + FP)，召回率 R = TP / (TP + FN)，F1 = 2PR / (P + R)；严格实体 F1 阈值 90%。",
        "- 唯一URL可访问率 = 可访问的已检查唯一URL数 / 已检查唯一URL数；阈值 98%。",
        "- 断言—URL支撑准确率 = 完全支撑关系数 / 全部公开断言—URL关系数；阈值 90%。部分支撑不计入完全支撑。",
        "- 分母为零或未完成测评时，指标显示“—”，不代表 0。",
        "", "## 实体抽取测评", "",
        f"状态：{_status(entity.get('status'))}", "",
    ]
    if entity.get("mode") == "proxy":
        lines.extend(["证据支撑率仅为代理指标，不能替代实体准确率。",
                      f"代理证据支撑率：{_rate(entity.get('proxy_evidence_support_rate'))}", ""])
    overall = _mapping(entity.get("overall"))
    lines.extend(["### 实体总体", ""])
    lines.extend(_table(("TP", "FP", "FN", "P", "R", "F1", "阈值结果"), [(
        _count(overall.get("true_positive")), _count(overall.get("false_positive")),
        _count(overall.get("false_negative")), _rate(overall.get("precision")),
        _rate(overall.get("recall")), _rate(overall.get("f1")),
        _verdict(overall.get("requirement_met")))]))
    lines.extend(["", "### 分类结果", ""])
    categories = _mapping(entity.get("categories"))
    category_rows = []
    for key in sorted(categories, key=str):
        item = _mapping(categories[key])
        category_rows.append((_text(item.get("label") or key), _count(item.get("true_positive")),
                              _count(item.get("false_positive")), _count(item.get("false_negative")),
                              _rate(item.get("precision")), _rate(item.get("recall")),
                              _rate(item.get("f1"))))
    lines.extend(_table(("分类", "TP", "FP", "FN", "P", "R", "F1"), category_rows))
    if not category_rows:
        lines.extend(["", "无分类结果或未执行严格实体评估。"])
    for key, title in (("matched", "Matched（正确匹配）"),
                       ("false_positives", "False positives（误报）"),
                       ("false_negatives", "False negatives（漏报）")):
        items = _list(entity.get(key))
        lines.extend(["", f"### {title}", ""])
        lines.extend(_table(("序号", "实体"), [(i, _entity_name(item)) for i, item in enumerate(items, 1)]))
        if not items:
            lines.extend(["", "无明细或未完成测评。"])

    lines.extend(["", "## 公开URL可访问性", "", f"状态：{_status(access.get('status'))}", "",
                  f"唯一URL可访问率：{_rate(access.get('rate'))}；阈值：98%；{_verdict(access.get('requirement_met'))}。",
                  f"唯一URL总数：{_count(access.get('total_count'))}；已检查：{_count(access.get('checked_count'))}；"
                  f"可访问：{_count(access.get('accessible_count'))}；不可访问：{_count(access.get('inaccessible_count'))}。",
                  "", "### 逐URL安全状态", ""])
    url_rows = []
    for item in _list(access.get("results")):
        row = _mapping(item)
        status = "可访问" if row.get("accessible") is True else "不可访问" if row.get("accessible") is False else "未检查"
        url_rows.append((_url(row.get("url")), status, _count(row.get("status_code")),
                         _text(row.get("failure_reason"), 80), _text(row.get("method"), 20)))
    lines.extend(_table(("URL", "安全状态", "HTTP状态", "失败原因", "方法"), url_rows))
    if not url_rows:
        lines.extend(["", "无公开URL明细。"])

    lines.extend(["", "## 断言—URL支撑测评", "", f"状态：{_status(support.get('status'))}", "",
                  f"断言—URL支撑准确率：{_rate(support.get('accuracy'))}；阈值：90%；{_verdict(support.get('requirement_met'))}。",
                  f"关系数：{_count(support.get('relationship_count'))}；完全支撑：{_count(support.get('supported_count'))}；"
                  f"部分支撑：{_count(support.get('partially_supported_count'))}；不支撑：{_count(support.get('unsupported_count'))}；"
                  f"未检查：{_count(support.get('unchecked_count'))}。",
                  "", "### 逐关系结果", ""])
    relation_rows = []
    for item in _list(support.get("relationships")):
        row = _mapping(item)
        status = row.get("status") if row.get("status") in _REASONS else "unchecked"
        relation_rows.append((_text(row.get("claim"), 500), _url(row.get("url")), status,
                              _rate(row.get("confidence")), _REASONS[status]))
    lines.extend(_table(("断言", "URL", "状态", "置信度", "固定原因"), relation_rows))
    if not relation_rows:
        lines.extend(["", "无公开断言—URL关系明细。"])

    lines.extend(["", "## 错误与降级说明", ""])
    errors = _list(safe.get("errors"))
    safe_errors = []
    for item in errors:
        row = _mapping(item)
        scope = row.get("scope")
        code = row.get("code")
        if scope in {"entity", "public_links", "style_cleanup", "evaluation_report_paths"} and code in {
                "evaluation_failed", "invalid_ground_truth", "invalid_path"}:
            safe_errors.append((scope, code, "标准答案文件无效。" if code == "invalid_ground_truth" else
                                "测评报告路径无效。" if code == "invalid_path" else "相关测评未完成。"))
    lines.extend(_table(("范围", "代码", "说明"), safe_errors))
    if not safe_errors:
        lines.extend(["", "无错误；未测评或无URL的项目不计为失败。"])
    lines.extend(["", f"规范清理数量（style removed_count）：{_count(cleanup.get('removed_count'))}。", ""])
    return "\n".join(lines)


def _safe_component(value: str, limit: int, fallback: str) -> str:
    candidate = unicodedata.normalize("NFKC", value if isinstance(value, str) else "")
    candidate = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]+', "_", candidate)
    candidate = re.sub(r"\s+", "_", candidate).strip(" ._")[:limit].rstrip(" ._")
    if not candidate:
        return fallback
    if candidate.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        candidate = "report_" + candidate
    return candidate


def _timestamp(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    except (AttributeError, TypeError, ValueError):
        return "undated_" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _write_markdown_atomic(destination: Path, markdown: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".evaluation-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(markdown)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _reserve_path(folder: Path, stem: str) -> Path:
    for index in range(10000):
        candidate = folder / f"{stem}{'_' + str(index) if index else ''}.md"
        if any(candidate.with_suffix(ext).exists() for ext in (".md", ".docx", ".pdf")):
            continue
        try:
            descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(descriptor)
        return candidate
    raise OSError("No available evaluation report filename")


async def export_evaluation_report(*, markdown: str, task: str, run_id: str,
                                   evaluated_at: str, output_dir: str | Path = "outputs/evaluations") -> dict:
    """Atomically save Markdown, then independently export Word and PDF."""
    result = {"markdown": "", "word": "", "pdf": "", "errors": []}
    path = None
    try:
        folder = Path(output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{_safe_component(task, 60, 'task')}_{_safe_component(run_id, 12, 'run')}_{_timestamp(evaluated_at)}"
        path = _reserve_path(folder, name)
        _write_markdown_atomic(path, markdown)
        result["markdown"] = f"{_PUBLIC_DIR}/{path.name}"
    except Exception:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        result["errors"].append({"format": "markdown", "code": "export_failed",
                                 "message": "Markdown测评报告导出失败。"})
        return result

    for kind, extension, renderer, message in (
        ("word", ".docx", render_word, "Word测评报告导出失败。"),
        ("pdf", ".pdf", render_pdf, "PDF测评报告导出失败。"),
    ):
        destination = path.with_suffix(extension)
        try:
            await asyncio.to_thread(renderer, markdown, destination, base_path=Path.cwd())
            result[kind] = f"{_PUBLIC_DIR}/{destination.name}"
        except Exception:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            result["errors"].append({"format": kind, "code": "export_failed", "message": message})
    return result
