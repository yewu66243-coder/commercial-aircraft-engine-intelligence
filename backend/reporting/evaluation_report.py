"""Render the stable evaluation summary as a standalone, offline report.

Only presentation fields from ``summary`` enter the document. Raw evaluator
results are accepted for the caller's existing interface but never serialized.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import html
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit, urlunsplit

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
_CREDENTIAL_PATTERNS = (
    re.compile(r"\bauthorization\s*:\s*[^\r\n|]+", re.IGNORECASE),
    re.compile(r"\b(?:x-api-key|api-key)\s*:\s*[^\r\n|]+", re.IGNORECASE),
    re.compile(r"\b(?:set-cookie|cookie)\s*:\s*[^\r\n|]+", re.IGNORECASE),
    re.compile(r"\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*[^\s|,;&]+", re.IGNORECASE),
)
_BARE_BEARER_PATTERN = re.compile(r"\b(bearer)\s+([^\s|;,]+)", re.IGNORECASE)
_MAX_DETAIL_ROWS = 500
TIME_LIMIT_SECONDS = 20 * 60


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _redact_credentials(value: str) -> str:
    decoded = value
    for _ in range(2):
        unquoted = unquote(decoded)
        if unquoted == decoded:
            break
        decoded = unquoted
    redacted = decoded
    for pattern in _CREDENTIAL_PATTERNS:
        redacted = pattern.sub("[凭据已隐藏]", redacted)
    redacted = _BARE_BEARER_PATTERN.sub(_redact_bare_bearer, redacted)
    return value if redacted == decoded else redacted


def _redact_bare_bearer(match: re.Match[str]) -> str:
    scheme, token = match.group(1), match.group(2)
    if scheme == "Bearer" or any(char.isdigit() or char.isupper() or char in "._-" for char in token):
        return "[凭据已隐藏]"
    return match.group(0)


def _text(value: object, limit: int = 500) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return "—"
    if isinstance(value, float) and not math.isfinite(value):
        return "—"
    result = _redact_credentials(str(value))
    result = re.sub(r"[\x00-\x1f\x7f]+", " ", result)
    result = re.sub(r"(?i)(?:[A-Z]:[\\/](?!/)|\\\\)[^\s|]+", "[路径已隐藏]", result)
    result = re.sub(r"(?i)(?<!\w)/(?:Users|home|tmp|var|private|mnt|etc)/[^\s|]+",
                    "[路径已隐藏]", result)
    result = result.strip()
    return (result[:limit] + "…") if len(result) > limit else result or "—"


def _cell(value: object, limit: int = 500) -> str:
    result = html.escape(_text(value, limit), quote=False)
    return re.sub(r"([\\|\[\]\(\)*_`])", r"\\\1", result)


def _rate(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    numeric = float(value)
    return f"{numeric * 100:.2f}".rstrip("0").rstrip(".") + "%" if math.isfinite(numeric) and 0 <= numeric <= 1 else "—"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and numeric >= 0 else None


def _plain_cell(value: object) -> str:
    return str(value if value is not None else "未记录").replace("|", "／").replace("\n", " ").replace("\r", " ")


def _plain_percent(value: object) -> str:
    numeric = _number(value)
    return f"{numeric:.2%}" if numeric is not None else "无法计算"


def assess_run(record: dict) -> dict:
    """Compatibility assessment used by the independent-judge workflow."""
    record = _mapping(record)
    seconds = _number(record.get("total_duration_seconds"))
    entity = _mapping(record.get("entity_eval"))
    source = _mapping(record.get("public_url_source_eval"))
    auto = _mapping(entity.get("auto_evidence_eval"))
    entity_score = entity.get("accuracy")
    entity_method = "标准答案名称匹配初筛；尚未独立核对数值、单位及语义，不能作为正式验收结论"
    if entity_score is None:
        entity_score = auto.get("strict_auto_evidence_accuracy") if auto.get("checked_count", 0) else None
        entity_method = "正文或内部表格候选的原文证据初筛；分母包含未核验候选，部分支撑计0.5分，不评价漏抽，不等同正式实体准确率"
    entity_value = _plain_percent(entity_score) if entity_score is not None else (
        "已提取候选，但未能读取对应证据" if entity.get("extracted_count", 0) else "未识别到实体或参数候选"
    )
    source_value = _plain_percent(source.get("support_accuracy")) if source.get("support_accuracy") is not None else (
        "本轮未引用公开网页，无链接样本" if not source.get("cited_url_ref_count", 0) else "公开链接未完成核验"
    )
    metrics = [
        {
            "id": "duration",
            "name": "深度检索及报告生成耗时",
            "threshold": "不超过20分钟",
            "value": f"{seconds / 60:.2f}分钟（{seconds:g}秒）" if seconds is not None else "未记录",
            "status": "待复核" if seconds is None else "达标" if seconds <= TIME_LIMIT_SECONDS else "未达标",
            "method": "后端任务开始至研究报告导出完成；不含浏览器网络传输和测评附件导出时间",
        },
        {
            "id": "entity",
            "name": "核心技术实体抽取准确率",
            "threshold": "不低于90%",
            "value": entity_value,
            "status": "待复核",
            "method": entity_method,
        },
        {
            "id": "source",
            "name": "公开信息溯源链接准确率",
            "threshold": "不低于98%",
            "value": source_value,
            "status": "待复核",
            "method": "现有规则按引用上下文关键词及数字匹配，部分支撑计0.5分，未核验计入分母；须由人工或模型裁判复核",
        },
    ]
    for item, score, threshold in ((metrics[1], entity_score, 0.9), (metrics[2], source.get("support_accuracy"), 0.98)):
        numeric = _number(score)
        item["screening_status"] = (
            "待补充测评条件" if numeric is None else "初筛达标" if numeric >= threshold else "初筛未达标"
        )
    judge = _mapping(record.get("independent_judge"))
    if judge.get("status") in {"completed", "partial"}:
        for item, kind in ((metrics[1], "entity"), (metrics[2], "source")):
            measured = _mapping(judge.get(kind))
            if not measured.get("total"):
                continue
            item.pop("screening_status", None)
            verified = measured.get("correct", 0) + measured.get("incorrect", 0)
            item["value"] = (
                _plain_percent(measured.get("accuracy")) + "（模型核验）"
                if verified
                else f"未完成核验（{measured['total']}项待核验）"
            )
            met = measured.get("requirement_met")
            item["status"] = "待复核" if not verified else "达标" if met is True else "未达标" if met is False else "待复核"
            item["method"] = (
                f"独立模型{judge.get('model', '')}逐项核验；正确{measured.get('correct', 0)}/总数{measured['total']}，"
                f"待核验{measured.get('unresolved', 0)}；待核验项保留在分母中，未全部核验时不作达标判定。"
                + str(measured.get("scope", ""))
            )
    statuses = [item["status"] for item in metrics]
    overall = "未达标" if "未达标" in statuses else "待复核" if "待复核" in statuses else "达标"
    return {"overall": overall, "metrics": metrics, "acceptance_ready": overall == "达标", "required_test_rounds": 10}


def render_evaluation_report(record: dict, assessment: dict) -> str:
    """Render the legacy per-run acceptance assessment as Markdown."""
    record = _mapping(record)
    assessment = _mapping(assessment)
    lines = [
        "# 情报报告单次指标测评报告",
        "",
        f"关联报告：{_plain_cell(record.get('report_title'))}",
        "",
        f"任务：{_plain_cell(record.get('task'))}",
        "",
        f"运行编号：{_plain_cell(record.get('run_id'))}",
        "",
        f"开始时间：{_plain_cell(record.get('started_at'))}；研究报告完成时间：{_plain_cell(record.get('completed_at'))}",
        "",
        f"本轮综合判定：{_plain_cell(assessment.get('overall'))}。本表依据任务说明书的20分钟、90%、98%要求生成。",
        "",
        "## 1 指标与判定",
        "",
        "| 指标 | 要求 | 实测或初筛结果 | 判定 |",
        "| --- | --- | --- | --- |",
    ]
    for item in _list(assessment.get("metrics")):
        verdict = item.get("status") if "screening_status" not in item else item.get("screening_status") + "；验收待复核"
        lines.append(
            f"| {_plain_cell(item.get('name'))} | {_plain_cell(item.get('threshold'))} | "
            f"{_plain_cell(item.get('value'))} | {_plain_cell(verdict)} |"
        )
    lines += ["", "## 2 测评方法与样本", ""]
    for item in _list(assessment.get("metrics")):
        lines += [f"{_plain_cell(item.get('name'))}：{_plain_cell(item.get('method'))}。", ""]
    entity = _mapping(record.get("entity_eval"))
    source = _mapping(record.get("public_url_source_eval"))
    judge = _mapping(record.get("independent_judge"))
    lines += [
        f"实体/参数候选数：{entity.get('extracted_count', 0)}；候选核验说明：{_plain_cell(entity.get('note', '未获得有效实体测评结果'))}",
        "",
        f"公开链接样本数：{source.get('cited_url_ref_count', 0)}；完全支撑 {source.get('supported_count', 0)}，"
        f"部分支撑 {source.get('partially_supported_count', 0)}，未支撑 {source.get('unsupported_count', 0)}，"
        f"未核验 {source.get('unchecked_count', 0)}。",
        "",
        "## 3 独立大模型裁判",
        "",
        f"裁判模型：{_plain_cell(judge.get('model') or '未配置')}；状态：{_plain_cell(judge.get('status') or '未启用')}。",
        "",
        _plain_cell(judge.get("message") or "此运行未执行独立裁判。"),
        "",
        "## 4 验收轮次要求",
        "",
        "任务说明书要求至少10轮测试，且每轮均满足三项指标。本文件只对应上述运行编号；未复核的初筛结果不能计作通过的验收轮次。",
        "",
    ]
    return "\n".join(lines)


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


def _truncated(items: list) -> tuple[list, bool]:
    return items[:_MAX_DETAIL_ROWS], len(items) > _MAX_DETAIL_ROWS


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
    category_keys, categories_truncated = _truncated(sorted(categories, key=str))
    for key in category_keys:
        item = _mapping(categories[key])
        category_rows.append((_text(item.get("label") or key), _count(item.get("true_positive")),
                              _count(item.get("false_positive")), _count(item.get("false_negative")),
                              _rate(item.get("precision")), _rate(item.get("recall")),
                              _rate(item.get("f1"))))
    lines.extend(_table(("分类", "TP", "FP", "FN", "P", "R", "F1"), category_rows))
    if categories_truncated:
        lines.extend(["", f"分类明细已截断，仅显示前{_MAX_DETAIL_ROWS}项。"])
    if not category_rows:
        lines.extend(["", "无分类结果或未执行严格实体评估。"])
    for key, title in (("matched", "Matched（正确匹配）"),
                       ("false_positives", "False positives（误报）"),
                       ("false_negatives", "False negatives（漏报）")):
        items, items_truncated = _truncated(_list(entity.get(key)))
        lines.extend(["", f"### {title}", ""])
        lines.extend(_table(("序号", "实体"), [(i, _entity_name(item)) for i, item in enumerate(items, 1)]))
        if not items:
            lines.extend(["", "无明细或未完成测评。"])
        elif items_truncated:
            lines.extend(["", f"明细已截断，仅显示前{_MAX_DETAIL_ROWS}项。"])

    lines.extend(["", "## 公开URL可访问性", "", f"状态：{_status(access.get('status'))}", "",
                  f"唯一URL可访问率：{_rate(access.get('rate'))}；阈值：98%；{_verdict(access.get('requirement_met'))}。",
                  f"唯一URL总数：{_count(access.get('total_count'))}；已检查：{_count(access.get('checked_count'))}；"
                  f"可访问：{_count(access.get('accessible_count'))}；不可访问：{_count(access.get('inaccessible_count'))}。",
                  "", "### 逐URL安全状态", ""])
    url_rows = []
    access_items, access_truncated = _truncated(_list(access.get("results")))
    for item in access_items:
        row = _mapping(item)
        status = "可访问" if row.get("accessible") is True else "不可访问" if row.get("accessible") is False else "未检查"
        url_rows.append((_url(row.get("url")), status, _count(row.get("status_code")),
                         _text(row.get("failure_reason"), 80), _text(row.get("method"), 20)))
    lines.extend(_table(("URL", "安全状态", "HTTP状态", "失败原因", "方法"), url_rows))
    if not url_rows:
        lines.extend(["", "无公开URL明细。"])
    elif access_truncated:
        lines.extend(["", f"URL明细已截断，仅显示前{_MAX_DETAIL_ROWS}项。"])

    lines.extend(["", "## 断言—URL支撑测评", "", f"状态：{_status(support.get('status'))}", "",
                  f"断言—URL支撑准确率：{_rate(support.get('accuracy'))}；阈值：90%；{_verdict(support.get('requirement_met'))}。",
                  f"关系数：{_count(support.get('relationship_count'))}；完全支撑：{_count(support.get('supported_count'))}；"
                  f"部分支撑：{_count(support.get('partially_supported_count'))}；不支撑：{_count(support.get('unsupported_count'))}；"
                  f"未检查：{_count(support.get('unchecked_count'))}。",
                  "", "### 逐关系结果", ""])
    relation_rows = []
    relationship_items, relationships_truncated = _truncated(_list(support.get("relationships")))
    for item in relationship_items:
        row = _mapping(item)
        status = row.get("status") if row.get("status") in _REASONS else "unchecked"
        relation_rows.append((_text(row.get("claim"), 500), _url(row.get("url")), status,
                              _rate(row.get("confidence")), _REASONS[status]))
    lines.extend(_table(("断言", "URL", "状态", "置信度", "固定原因"), relation_rows))
    if not relation_rows:
        lines.extend(["", "无公开断言—URL关系明细。"])
    elif relationships_truncated:
        lines.extend(["", f"关系明细已截断，仅显示前{_MAX_DETAIL_ROWS}项。"])

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
    candidate = "".join(char if char.isalnum() or char in "_.-" else "_" for char in candidate)
    candidate = candidate.strip(" ._")[:limit].rstrip(" ._")
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


def _reserve_path(folder: Path, stem: str) -> tuple[Path, Path]:
    for index in range(10000):
        base = folder / f"{stem}{'_' + str(index) if index else ''}"
        candidate, lock = base.parent / f"{base.name}.md", folder / f".{base.name}.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(descriptor)
        if any((base.parent / f"{base.name}{ext}").exists() for ext in (".md", ".docx", ".pdf")):
            lock.unlink(missing_ok=True)
            continue
        return candidate, lock
    raise OSError("No available evaluation report filename")


def _temporary_path(folder: Path, stem: str, suffix: str) -> Path:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{stem}-", suffix=f"{suffix}.tmp", dir=folder)
    os.close(descriptor)
    return Path(temporary)


def _artifact_path(markdown_path: Path, extension: str) -> Path:
    base_name = markdown_path.name.removesuffix(".md")
    return markdown_path.parent / f"{base_name}{extension}"


def _publish_no_overwrite(temporary: Path, destination: Path) -> None:
    """Publish within one filesystem without replacing an existing report."""
    os.link(temporary, destination)
    temporary.unlink(missing_ok=True)


async def _render_to_temporary(renderer, markdown: str, temporary: Path) -> None:
    task = asyncio.create_task(asyncio.to_thread(renderer, markdown, temporary, base_path=Path.cwd()))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        except Exception:
            pass
        raise


async def export_evaluation_report(*, markdown: str, task: str, run_id: str,
                                   evaluated_at: str, output_dir: str | Path = "outputs/evaluations") -> dict:
    """Safely publish independent Markdown, Word, and PDF report artifacts."""
    folder = Path(output_dir)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        return {"markdown": "", "word": "", "pdf": "", "errors": [
            {"format": "markdown", "code": "export_failed", "message": "Markdown测评报告导出失败。"}]}
    stem = f"{_safe_component(task, 60, 'task')}_{_safe_component(run_id, 12, 'run')}_{_timestamp(evaluated_at)}"
    try:
        for _ in range(10000):
            result = {"markdown": "", "word": "", "pdf": "", "errors": []}
            path, lock = _reserve_path(folder, stem)
            temporary, published = [], []
            try:
                markdown_temp = _temporary_path(folder, path.name.removesuffix(".md"), ".md")
                temporary.append(markdown_temp)
                _write_markdown_atomic(markdown_temp, markdown)
                try:
                    _publish_no_overwrite(markdown_temp, path)
                except FileExistsError:
                    continue
                temporary.remove(markdown_temp)
                published.append(path)
                result["markdown"] = f"{_PUBLIC_DIR}/{path.name}"
                for kind, extension, renderer, message in (
                    ("word", ".docx", render_word, "Word测评报告导出失败。"),
                    ("pdf", ".pdf", render_pdf, "PDF测评报告导出失败。"),
                ):
                    try:
                        destination = _artifact_path(path, extension)
                        render_temp = _temporary_path(folder, path.name.removesuffix(".md"), extension)
                        temporary.append(render_temp)
                        await _render_to_temporary(renderer, markdown, render_temp)
                        _publish_no_overwrite(render_temp, destination)
                        temporary.remove(render_temp)
                        published.append(destination)
                        result[kind] = f"{_PUBLIC_DIR}/{destination.name}"
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        result["errors"].append({"format": kind, "code": "export_failed", "message": message})
                return result
            except asyncio.CancelledError:
                for item in published:
                    item.unlink(missing_ok=True)
                raise
            except Exception:
                result["errors"].append({"format": "markdown", "code": "export_failed",
                                         "message": "Markdown测评报告导出失败。"})
                return result
            finally:
                for item in temporary:
                    item.unlink(missing_ok=True)
                lock.unlink(missing_ok=True)
    except Exception:
        return {"markdown": "", "word": "", "pdf": "", "errors": [
            {"format": "markdown", "code": "export_failed", "message": "Markdown测评报告导出失败。"}]}
    return {"markdown": "", "word": "", "pdf": "", "errors": [
        {"format": "markdown", "code": "export_failed", "message": "Markdown测评报告导出失败。"}]}
