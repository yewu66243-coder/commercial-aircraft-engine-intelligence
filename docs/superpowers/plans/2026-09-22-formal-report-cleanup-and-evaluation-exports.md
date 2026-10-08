# 正式报告规范清理与测评报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 确定性删除正式报告中的自我指涉免责句，并在报告生成后或用户点击“重新测评”后，生成实体抽取、公开链接可访问性和断言支撑准确率的稳定页面摘要及独立 Word/PDF 测评报告。

**Architecture:** 把“输入解析、正文清理、三类评分、稳定摘要、测评报告导出、历史记录”拆成可独立测试的纯组件；`three_agent_service.py` 只负责编排，`main.py` 只负责 HTTP 边界。自动测评与重新测评复用同一个 `evaluate_saved_report` 入口，且都以校订、清理后的 `evidence_report` 为唯一评分正文。所有测评错误均转换为分项状态，不阻断原研究报告导出。

**Tech Stack:** Python 3.11、FastAPI、pytest、openpyxl、原生 JavaScript、Node.js 语法检查、现有 `document_export.py` Word/PDF 渲染器。

---

## 实施基线

- 在分支 `codex/entity-evaluation-dashboard` 和工作树 `C:\Users\吴烨\.codex\worktrees\entity-evaluation\commercial-aircraft-engine-intelligence` 中实施。
- 当前分支已经具备实体匹配器、公开 URL 支撑评估器、三卡片测评面板和运行记录；本计划只扩展已确认设计，不重建现有能力。
- 所有测试命令使用主工作区虚拟环境：

```powershell
$python = 'C:\Users\吴烨\Documents\GitHub\commercial-aircraft-engine-intelligence\.venv\Scripts\python.exe'
```

- 每个任务严格按“先失败测试、再最小实现、再重构、再提交”执行。

## Task 1：增加 Excel 依赖并建立标准答案统一输入层

**Files:**

- Modify: `requirements.txt`
- Modify: `backend/requirements.txt`
- Modify: `pyproject.toml`
- Create: `gpt_researcher/evaluation/ground_truth_io.py`
- Create: `tests/test_ground_truth_io.py`

- [ ] **Step 1: 为 JSON/XLSX 等价解析写失败测试**

在 `tests/test_ground_truth_io.py` 创建最小合法 JSON 和内存 XLSX，断言两者产生相同统一结构：

```python
from pathlib import Path

from openpyxl import Workbook

from gpt_researcher.evaluation.ground_truth_io import (
    GroundTruthValidationError,
    load_ground_truth_upload,
    persist_ground_truth_upload,
)


def test_json_and_xlsx_normalize_to_same_entities(tmp_path: Path):
    json_path = tmp_path / "truth.json"
    json_path.write_text(
        '{"task":"GTF","entities":['
        '{"type":"机构","name":"Pratt & Whitney","aliases":["普惠"]},'
        '{"type":"参数","name":"起飞推力","value":33110,"unit":"lbf","tolerance":0.01}'
        ']}',
        encoding="utf-8",
    )
    xlsx_path = tmp_path / "truth.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "标准答案"
    sheet.append(["类别", "名称", "别名", "数值", "单位", "容差"])
    sheet.append(["机构", "Pratt & Whitney", "普惠", None, None, None])
    sheet.append(["参数", "起飞推力", None, 33110, "lbf", 0.01])
    book.save(xlsx_path)

    json_truth = load_ground_truth_upload(json_path, expected_task="GTF")
    xlsx_truth = load_ground_truth_upload(xlsx_path, expected_task="GTF")

    assert json_truth["entities"] == xlsx_truth["entities"]
```

- [ ] **Step 2: 为所有校验边界写参数化失败测试**

覆盖：不允许的扩展名、空文件、超过 5 MiB、JSON 非对象、任务名不一致、空实体数组、缺失类别/名称、别名类型错误、非法数值、负数或非有限容差、重复实体、Excel 缺列、公式单元格没有缓存值、错误中包含工作表名和 Excel 行号。另写目录穿越文件名测试，断言持久化结果始终位于目标目录内。

错误对象固定为：

```python
class GroundTruthValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
```

- [ ] **Step 3: 运行测试并确认依赖或模块缺失**

Run:

```powershell
& $python -m pytest tests/test_ground_truth_io.py -q
```

Expected: FAIL，首个失败为 `ModuleNotFoundError: No module named 'openpyxl'` 或 `ground_truth_io` 尚不存在。

- [ ] **Step 4: 在三个依赖入口加入同一版本约束并安装**

增加 `openpyxl>=3.1.5`：

- `requirements.txt` 的 Document Processing 段；
- `backend/requirements.txt` 的 Output formats 段；
- `pyproject.toml` 的 `[tool.poetry.dependencies]` 和 `[project].dependencies`。

Run:

```powershell
& $python -m pip install "openpyxl>=3.1.5"
```

Expected: exit 0，输出包含成功安装或 already satisfied。

- [ ] **Step 5: 实现统一解析、校验和安全持久化**

`ground_truth_io.py` 暴露以下稳定 API：

```python
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_SUFFIXES = {".json", ".xlsx"}


def load_ground_truth_upload(path: str | Path, expected_task: str) -> dict[str, object]:
    """Parse JSON/XLSX, inject or validate task, and return canonical entities."""


def persist_ground_truth_upload(
    content: bytes,
    original_name: str,
    task: str,
    destination_dir: str | Path,
) -> dict[str, object]:
    """Validate in a temporary sibling file, then atomically replace the active task file."""


def ground_truth_path_for_task(task: str, destination_dir: str | Path) -> Path:
    digest = hashlib.sha256(task.encode("utf-8")).hexdigest()[:20]
    return Path(destination_dir).resolve() / f"{digest}.json"
```

实现约束：

- XLSX 使用 `load_workbook(path, read_only=True, data_only=True, keep_links=False)`；读取第一个可见工作表；禁止 `.xlsm`。
- Excel 的 `别名` 按 `;` 和 `；` 分隔；空白行忽略。
- 统一实体字段为 `type`、`name`、`aliases`，参数可带 `value`、`unit`、`tolerance`；`tolerance` 默认 `0.01`。
- JSON 与 Excel 都调用同一个 `_validate_canonical_ground_truth`。
- 重复键为归一类别、归一名称、归一数值和单位的组合；重复时抛错，禁止静默去重。
- 上传成功后保存规范 JSON，而不是原 XLSX；返回 `stored_name`、`sha256`、`entity_count`、`category_counts`。
- 临时文件由 `tempfile.NamedTemporaryFile(delete=False, dir=destination_dir)` 创建，并在 `finally` 删除；最终替换使用 `os.replace`。

- [ ] **Step 6: 让现有实体评估器使用统一读取器**

修改 `gpt_researcher/evaluation/entity_evaluator.py`：保留 `load_ground_truth` 公共函数，内部委托给统一校验器；已有严格匹配逻辑和返回结构不改。补充测试证明原 JSON 调用仍兼容。

- [ ] **Step 7: 运行输入层和实体回归测试**

Run:

```powershell
& $python -m pytest tests/test_ground_truth_io.py tests/test_entity_evaluation_metrics.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交**

```powershell
git add requirements.txt backend/requirements.txt pyproject.toml gpt_researcher/evaluation/ground_truth_io.py gpt_researcher/evaluation/entity_evaluator.py tests/test_ground_truth_io.py tests/test_entity_evaluation_metrics.py
git commit -m "feat: accept json and xlsx entity ground truth"
```

## Task 2：实现确定性的正式报告语句清理器

**Files:**

- Modify: `backend/reporting/finalization.py`
- Create: `tests/test_formal_style_cleanup.py`

- [ ] **Step 1: 用用户六张截图中的原句写失败测试**

在 `tests/test_formal_style_cleanup.py` 参数化覆盖至少以下表达：

```python
PROHIBITED_EXAMPLES = [
    "受资料范围限制，本报告未取得 CCAR-33、FAR Part 33、CS-E 标准原文及适航指令原文，不对三大标准的条款协调性作结论。",
    "因此涉及具体适航指令编号、生效日期、检查改装要求及三大标准条款协调性的内容，本报告不作确定性结论。",
    "文献未将其与 FADEC 转速匹配或瞬态响应算法的验证建立关联，本报告不作此类推断。",
    "文献仅将电子控制器软件优化列为升级内容之一，未说明其发布形式、验证要求或是否触发运行包线重新验证，本报告不作此类定性。",
    "文献未说明认证缺失对运营范围的具体影响，本报告不作该因果推断。",
    "文献未涉及在役机队持续适航指令，本报告不对两类监管行为的性质作区分性判断。",
]


@pytest.mark.parametrize("sentence", PROHIBITED_EXAMPLES)
def test_cleanup_removes_user_examples(sentence):
    cleaned, audit = clean_formal_report_style(f"## 结论\n\n有效结论。[URL1] {sentence}\n")
    assert sentence not in cleaned
    assert "有效结论。[URL1]" in cleaned
    assert audit["removed_count"] == 1
```

- [ ] **Step 2: 写防误删和结构清理失败测试**

断言下列内容保留：

- `样本覆盖 2024—2026 年公开资料。`
- `本次共纳入 15 份可读取原文。`
- `公开资料未披露该部件价格。`（没有报告自我指涉或“不作判断”动作）
- 客观方法边界、来源数、检索日期。

另覆盖：混合段落仅删违规句；删句后孤立 `[URL1]` 被移除；只剩标点/连接词的段落被移除；表格单元格置 `—`；整行没有实质内容时删除；审计包含章节、规则、原文，但清理后的正文不包含原文。

- [ ] **Step 3: 运行测试确认失败**

```powershell
& $python -m pytest tests/test_formal_style_cleanup.py -q
```

Expected: FAIL，`clean_formal_report_style` 尚不存在。

- [ ] **Step 4: 在 `finalization.py` 实现句级清理**

新增稳定入口：

```python
def clean_formal_report_style(report: str) -> tuple[str, dict[str, object]]:
    """Remove formal-report disclaimers and return cleaned Markdown plus audit data."""
```

实现为确定性状态机：

1. 按 Markdown 行遍历并跟踪最近的 `#` 标题；
2. 普通段落按中文/英文句末符分句，同时保留原分隔符；
3. 只有命中以下联合条件才删：
   - `本报告` + `不作|不进行|不据此|无法据此` + `判断|推断|结论|区分|定性`；或
   - `文献|资料|原文` + `未说明|未涉及|未取得|未建立`，同句再含否定结论动作；或
   - `受资料范围限制|鉴于证据不足` 开头，且剩余内容只表达不判断动作；或
   - 前半句为断言、后半句以 `因此无法证实|故不作确定性结论` 撤回断言，仅删撤回分句。
4. 规则不能只凭 `未`、`不足`、`限制` 单词命中。
5. 删除命中句绑定的紧邻行内引用；清理空括号、孤立引用、重复空格和空段。
6. 表格逐单元格清理；空单元格写 `—`；除首列标识外都为空的正文行删除。

审计固定结构：

```python
audit = {
    "version": "formal-style-cleanup-v1",
    "removed_count": len(removed_items),
    "removed_items": removed_items,
}
```

每个 `removed_items` 元素只有 `section`、`rule`、`text`，不包含上下文全文。

- [ ] **Step 5: 运行清理测试与成稿回归**

```powershell
& $python -m pytest tests/test_formal_style_cleanup.py tests/test_report_finalization.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add backend/reporting/finalization.py tests/test_formal_style_cleanup.py tests/test_report_finalization.py
git commit -m "fix: remove formal report disclaimer sentences"
```

## Task 3：按“断言—链接”关系修正公开来源支撑准确率

**Files:**

- Modify: `gpt_researcher/evaluation/source_evaluator.py`
- Create: `tests/test_public_source_relationships.py`
- Modify: `tests/test_formal_pipeline.py`

- [ ] **Step 1: 写同一 URL 多断言的失败测试**

构造两段正文都引用 `[URL1]`，替换 `_read_url_text` 和 `_check_claims_against_source`，让一条 `supported`、一条 `partially_supported`。断言：

```python
assert result["relationship_count"] == 2
assert result["supported_count"] == 1
assert result["partially_supported_count"] == 1
assert result["support_accuracy"] == 0.5
assert len(result["relationships"]) == 2
```

再覆盖一条断言同时引用 `[URL1][URL2]` 应产生两条关系，以及 `unchecked` 必须进入分母。

- [ ] **Step 2: 运行测试确认当前按 URL 聚合且部分支撑加权**

```powershell
& $python -m pytest tests/test_public_source_relationships.py -q
```

Expected: FAIL，当前 `relationship_count`/`relationships` 不存在，且准确率仍按 URL 加权。

- [ ] **Step 3: 将提取结果展开为稳定关系列表**

保留 `_extract_url_reference_contexts` 兼容现有引用清理，新增：

```python
def _extract_url_claim_relationships(report: str) -> list[dict[str, str]]:
    relationships = []
    for ref, claims in _extract_url_reference_contexts(report).items():
        for index, claim in enumerate(claims, start=1):
            relationships.append({
                "relationship_id": f"{_normalize_ref(ref)}:{index}",
                "ref": _normalize_ref(ref),
                "claim": claim,
            })
    return relationships
```

每个唯一 URL 只抓取一次正文，然后对该 URL 的每个断言分别调用 `_check_claims_against_source([claim], source_text)`。返回：

```python
{
    "method": "claim_url_relationship_source_text_matching",
    "threshold": threshold,
    "unique_url_count": unique_url_count,
    "relationship_count": relationship_count,
    "supported_count": supported,
    "partially_supported_count": partial,
    "unsupported_count": unsupported,
    "unchecked_count": unchecked,
    "support_accuracy": supported / relationship_count if relationship_count else None,
    "requirement_met": accuracy is not None and accuracy >= threshold,
    "relationships": relationships,
    "results": per_ref_results,
}
```

`results` 继续按引用编号聚合，供 `prune_redundant_unchecked_url_citations` 使用；它的 `status` 取该引用关系中的最差状态，优先级为 `unchecked > unsupported > partially_supported > supported`。

- [ ] **Step 4: 删除加权正确率语义并更新日志断言**

删除 `weighted_score` 和 `checked_support_accuracy` 的产品语义。`partially_supported`、`unsupported`、`unchecked` 都只进分母。更新 `tests/test_formal_pipeline.py` 中旧字段期望，不改变引用清理行为。

- [ ] **Step 5: 运行来源和流水线测试**

```powershell
& $python -m pytest tests/test_public_source_relationships.py tests/test_formal_pipeline.py tests/test_evaluation_pipeline.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add gpt_researcher/evaluation/source_evaluator.py tests/test_public_source_relationships.py tests/test_formal_pipeline.py tests/test_evaluation_pipeline.py
git commit -m "fix: score public sources by claim link relationship"
```

## Task 4：抽取可复用的唯一链接可访问性评估器

**Files:**

- Create: `gpt_researcher/evaluation/link_accessibility.py`
- Modify: `three_agent_service.py`
- Create: `tests/test_link_accessibility.py`
- Modify: `tests/test_evaluation_pipeline.py`

- [ ] **Step 1: 写唯一 URL、无链接和错误分类失败测试**

断言重复 URL 只检查一次；相同 URL 的末尾标点被清理；无 URL 返回 `no_public_urls` 语义；200/302 可访问，403/404/5xx、超时、DNS、TLS 和连接失败不可访问。网络测试替换 `_check_url_sync`，不访问公网。

- [ ] **Step 2: 运行测试确认模块不存在**

```powershell
& $python -m pytest tests/test_link_accessibility.py -q
```

Expected: FAIL。

- [ ] **Step 3: 从服务类迁移纯函数**

`link_accessibility.py` 暴露三个稳定入口：`extract_public_urls(report: str) -> list[str]`、`check_url_sync(url: str, timeout: int = 6) -> dict[str, object]`、`evaluate_link_accessibility(report: str, max_urls: int | None = None, checker: Callable = check_url_sync) -> dict[str, object]`。

返回沿用现有字段 `total_urls`、`checked_urls`、`accessible_urls`、`failed_urls`、`accessibility_rate`、`results`，其中 `total_urls` 明确表示唯一 URL 数。

- [ ] **Step 4: 保持服务兼容委托**

`ThreeAgentService._check_url_sync` 和 `inspect_report_urls` 保留现有可调用签名，但内部委托新模块；这样旧测试和其他调用者无需一次性改名。重新测评服务直接调用新模块，不实例化研究服务。

- [ ] **Step 5: 运行测试**

```powershell
& $python -m pytest tests/test_link_accessibility.py tests/test_evaluation_pipeline.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add gpt_researcher/evaluation/link_accessibility.py three_agent_service.py tests/test_link_accessibility.py tests/test_evaluation_pipeline.py
git commit -m "refactor: reuse public link accessibility evaluation"
```

## Task 5：升级稳定测评摘要为双链接指标和四卡片结构

**Files:**

- Modify: `gpt_researcher/evaluation/evaluation_summary.py`
- Modify: `tests/test_evaluation_summary.py`

- [ ] **Step 1: 先把测试改成已确认的稳定结构**

调用签名改为：

```python
summary = build_evaluation_summary(
    entity_eval=entity_eval,
    url_check=url_check,
    url_source_eval=url_source_eval,
    style_cleanup=style_cleanup,
    evaluation_report_paths=report_paths,
)
```

测试断言：

```python
assert summary["public_links"]["accessibility"]["rate"] == 0.99
assert summary["public_links"]["claim_support"]["accuracy"] == 0.925
assert summary["style_cleanup"] == {"removed_count": 3}
assert summary["evaluation_report_paths"]["word"].endswith(".docx")
```

覆盖严格实体、代理实体、无链接、三个分项任一失败、多个失败、0.98/0.90 边界、缺少新参数的向后兼容。

- [ ] **Step 2: 运行确认旧结构失败**

```powershell
& $python -m pytest tests/test_evaluation_summary.py -q
```

Expected: FAIL，`public_links.accessibility` 和 `claim_support` 尚不存在。

- [ ] **Step 3: 实现纯聚合器**

常量：

```python
ENTITY_THRESHOLD = 0.90
PUBLIC_LINK_ACCESSIBILITY_THRESHOLD = 0.98
PUBLIC_LINK_SUPPORT_THRESHOLD = 0.90
```

返回结构与设计文档完全一致：

- `entity.overall` 保留 Precision/Recall/F1 和 `requirement_met`；
- `public_links.accessibility` 使用唯一 URL 指标；
- `public_links.claim_support` 使用关系指标；
- `public_links.details` 保存可访问性逐 URL 与支撑逐关系结果；
- `style_cleanup` 只公开 `removed_count`；
- `evaluation_report_paths` 统一为相对静态路径；
- `errors` 每项含 `scope`、`code`、安全消息。

顶层状态：无错误为 `completed`；部分分项成功为 `partial`；所有可用分项失败为 `failed`。`no_public_urls` 不算错误。

- [ ] **Step 4: 运行摘要测试**

```powershell
& $python -m pytest tests/test_evaluation_summary.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add gpt_researcher/evaluation/evaluation_summary.py tests/test_evaluation_summary.py
git commit -m "feat: expose entity and dual link evaluation summary"
```

## Task 6：生成独立 Markdown、Word、PDF 测评报告

**Files:**

- Create: `backend/reporting/evaluation_report.py`
- Create: `tests/test_evaluation_report.py`

- [ ] **Step 1: 为完整、代理、部分失败三种报告写失败测试**

测试 `render_evaluation_report_markdown` 包含：任务/报告标识/时间、指标定义和阈值、总体与分类实体表、匹配/误抽/漏抽、唯一 URL 可访问明细、断言—URL 支撑明细、错误和降级说明、规范清理数量。断言它不包含 `style_cleanup.removed_items[*].text`，也不复制原研究正文。

导出测试替换 `render_word`、`render_pdf`，断言两者收到完全相同的 Markdown；任一格式抛错时另一格式路径仍返回。

- [ ] **Step 2: 运行确认失败**

```powershell
& $python -m pytest tests/test_evaluation_report.py -q
```

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现渲染器和导出器**

稳定 API 为 `render_evaluation_report_markdown(*, task: str, run_id: str, evaluated_at: str, summary: dict[str, object], entity_eval: dict[str, object], url_check: dict[str, object], url_source_eval: dict[str, object]) -> str` 和 `export_evaluation_report(*, markdown: str, task: str, run_id: str, evaluated_at: str, output_dir: str | Path = "outputs/evaluations") -> dict[str, object]`。

文件基名由安全任务名、`run_id` 前 12 位和 UTC 时间组成。返回：

```python
{
    "markdown": "/outputs/evaluations/GTF_run-123_20260922T120000Z.md",
    "word": "/outputs/evaluations/GTF_run-123_20260922T120000Z.docx",
    "pdf": "/outputs/evaluations/GTF_run-123_20260922T120000Z.pdf",
    "errors": [],
}
```

实现要求：

- 创建 `outputs/evaluations`；
- Markdown 用 UTF-8 原子写入；
- Word/PDF 复用 `backend.reporting.document_export.render_word/render_pdf` 并通过 `asyncio.to_thread` 调用；
- 路径只返回以 `/outputs/evaluations/` 开头的静态下载路径；
- 每种导出单独捕获异常，错误消息只含格式和异常类型，不含文件内容。

- [ ] **Step 4: 运行报告测试**

```powershell
& $python -m pytest tests/test_evaluation_report.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/reporting/evaluation_report.py tests/test_evaluation_report.py
git commit -m "feat: export standalone evaluation reports"
```

## Task 7：建立可重新测评的运行记录仓储与统一编排服务

**Files:**

- Create: `gpt_researcher/evaluation/records.py`
- Create: `gpt_researcher/evaluation/report_evaluation.py`
- Modify: `three_agent_service.py`
- Create: `tests/test_evaluation_records.py`
- Create: `tests/test_report_reevaluation.py`
- Modify: `tests/test_evaluation_pipeline.py`
- Modify: `tests/test_formal_pipeline.py`

- [ ] **Step 1: 为记录查询、追加历史和原子写入写失败测试**

`tests/test_evaluation_records.py` 覆盖：空仓储、按 `run_id` 命中、未命中、损坏 JSON 备份、并发写入通过进程内锁串行化、追加 reevaluation 不覆盖首次记录。`EvaluationRecordStore` 提供 `append_run(record)`、`get_run(run_id)` 和 `append_reevaluation(run_id, result)` 三个公开方法。

- [ ] **Step 2: 为重新测评不触发研究模型写失败测试**

测试替换实体、链接和导出组件，传入已有记录中的 `evidence_report`，断言：

- 使用保存的清理后正文；
- 不创建 `ThreeAgentService`，不调用 `planner_agent`、`research_agent`、`writer_agent`、`editorial_agent`；
- 新结果有新的 `evaluated_at` 和下载路径；
- 历史数组增加一条；
- 缺 `evidence_report` 时抛出 `SavedReportUnavailableError`。

- [ ] **Step 3: 运行确认失败**

```powershell
& $python -m pytest tests/test_evaluation_records.py tests/test_report_reevaluation.py -q
```

Expected: FAIL。

- [ ] **Step 4: 实现统一评测编排**

`report_evaluation.py` 暴露 `evaluate_saved_report(*, task: str, run_id: str, report: str, style_cleanup: dict[str, object] | None, ground_truth_path: str | Path | None, output_dir: str | Path = "outputs/evaluations") -> dict[str, object]`。函数依次并发取得 `url_check`、`url_source_eval`、`entity_eval`，先建立不含下载路径的摘要并渲染测评 Markdown，导出后再用真实 `report_paths` 建立最终摘要；返回 `evaluated_at`、三个原始结果、`evaluation_summary` 和 `evaluation_report_paths`。

实体、唯一 URL 可访问性、断言支撑三个分项各自捕获异常并返回失败状态，互不取消。禁止在此函数中清理或修改正文。

- [ ] **Step 5: 调整自动生成流水线顺序**

在 `three_agent_service.py` 把主流程固定为：

```python
draft = await self.writer_agent(sections)
draft = self.ensure_report_title(draft)
draft = self.ensure_report_images_inserted(draft)
draft = await self.editorial_agent(draft)
cleaned_report, style_cleanup = clean_formal_report_style(draft)
evaluation = await evaluate_saved_report(
    task=self.request.task,
    run_id=run_id,
    report=cleaned_report,
    style_cleanup=style_cleanup,
    ground_truth_path=resolve_active_ground_truth_path(self.request.task),
)
```

然后：

- `audit_report = cleaned_report`；
- `prepare_formal_report`、Markdown/Word/PDF 原报告导出全部使用 `cleaned_report`；
- `run_stats` 写入 `report_style_cleanup`、三个原始评分结果、稳定摘要和 `evaluation_report_paths`；
- 原报告导出先后顺序不受测评报告某一格式失败影响；
- `validation_summary.url_requirement_met` 使用严格断言关系准确率；
- 运行记录 `record_version` 升到 `3.0.0`；
- 删除原流水线在校订前评分再条件重算的分支，避免同一报告出现两个口径。

- [ ] **Step 6: 用仓储替换服务内直接 JSON 读写**

`append_evaluation_record` 可保留兼容方法，但只调用 `EvaluationRecordStore.append_run`。记录必须在原报告和测评报告路径确定后一次落盘；重新测评通过 `append_reevaluation` 加到原记录的 `reevaluations` 数组。

- [ ] **Step 7: 运行编排与完整流水线测试**

```powershell
& $python -m pytest tests/test_evaluation_records.py tests/test_report_reevaluation.py tests/test_evaluation_pipeline.py tests/test_formal_pipeline.py tests/test_report_finalization.py -q
```

Expected: PASS；测试中的导出和网络都已替换，不访问外网。

- [ ] **Step 8: 提交**

```powershell
git add gpt_researcher/evaluation/records.py gpt_researcher/evaluation/report_evaluation.py three_agent_service.py tests/test_evaluation_records.py tests/test_report_reevaluation.py tests/test_evaluation_pipeline.py tests/test_formal_pipeline.py tests/test_report_finalization.py
git commit -m "feat: evaluate cleaned reports and preserve reevaluation history"
```

## Task 8：增加标准答案上传和重新测评 API

**Files:**

- Modify: `main.py`
- Create: `tests/test_evaluation_api.py`

- [ ] **Step 1: 写 API 失败测试**

使用 `fastapi.testclient.TestClient` 覆盖：

- `POST /api/evaluation-ground-truth` 上传合法 JSON；
- 上传合法 XLSX；
- 缺 task、空文件、错误扩展名、超限、损坏文件、任务名不一致返回 400/413/422 和安全 `detail`；
- `POST /api/report-evaluation/{run_id}` 命中记录并返回新摘要和路径；
- 未知 `run_id` 返回 404；
- 已保存正文缺失返回 409；
- 重新测评内部单项失败仍返回 200 和 `partial`/`failed` 摘要。

- [ ] **Step 2: 运行确认路由不存在**

```powershell
& $python -m pytest tests/test_evaluation_api.py -q
```

Expected: FAIL，404。

- [ ] **Step 3: 实现上传路由**

在 `main.py` 增加：

```python
@app.post("/api/evaluation-ground-truth")
async def upload_evaluation_ground_truth(
    task: str = Form(...),
    file: UploadFile = File(...),
):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="标准答案文件不能超过 5 MiB。")
    try:
        return persist_ground_truth_upload(
            content,
            file.filename or "",
            task,
            Path("outputs/records/entity_ground_truths"),
        )
    except GroundTruthValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
```

文件名不得进入服务器路径；响应只返回安全文件名和摘要。

- [ ] **Step 4: 实现重新测评路由**

```python
@app.post("/api/report-evaluation/{run_id}")
async def reevaluate_report(run_id: str):
    record = evaluation_record_store.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="未找到该报告记录。")
    report = str(record.get("evidence_report") or "")
    if not report.strip():
        raise HTTPException(status_code=409, detail="该记录缺少可重新测评的最终正文。")
    result = await evaluate_saved_report(
        task=str(record["task"]),
        run_id=run_id,
        report=report,
        style_cleanup=record.get("report_style_cleanup"),
        ground_truth_path=resolve_active_ground_truth_path(str(record["task"])),
    )
    evaluation_record_store.append_reevaluation(run_id, result)
    return result
```

从记录读取 task、style cleanup；标准答案始终解析当前任务的活动文件，因此替换后无需重新生成报告。

- [ ] **Step 5: 运行 API 和安全回归**

```powershell
& $python -m pytest tests/test_evaluation_api.py tests/test_security_fix.py tests/test_model_provider_selection.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add main.py tests/test_evaluation_api.py
git commit -m "feat: add evaluation upload and rerun endpoints"
```

## Task 9：把前端升级为四卡片和完整测评操作区

**Files:**

- Modify: `frontend/index.html`
- Modify: `frontend/evaluation_panel.js`
- Modify: `frontend/scripts.js`
- Modify: `frontend/styles.css`
- Modify: `tests/js/check_evaluation_panel.cjs`
- Modify: `tests/test_evaluation_dashboard_ui.py`

- [ ] **Step 1: 先扩展 JavaScript 视图模型测试**

在 `tests/js/check_evaluation_panel.cjs` 构造严格、代理、无链接、标准答案错误、分项失败五种摘要，断言卡片顺序和文本：

1. 实体 Precision；
2. 实体 Recall / F1；
3. 链接可访问率；
4. 结论支撑准确率。

严格模式分别显示百分比和“达标/未达标”；代理模式显示“待标准答案”，并单独显示代理证据支撑率；`null` 显示 `—`；无链接显示“无公开链接”。

- [ ] **Step 2: 扩展静态结构失败测试**

`tests/test_evaluation_dashboard_ui.py` 断言存在：

- `evaluationEntityPrecision`
- `evaluationEntityRecallF1`
- `evaluationLinkAccessibility`
- `evaluationClaimSupport`
- `evaluationGroundTruthInput`
- `evaluationUploadButton`
- `evaluationRerunButton`
- `evaluationDownloadWord`
- `evaluationDownloadPdf`
- 当前标准答案状态文本和 ARIA live 区域。

- [ ] **Step 3: 运行前端测试确认失败**

```powershell
node tests/js/check_evaluation_panel.cjs
& $python -m pytest tests/test_evaluation_dashboard_ui.py -q
```

Expected: FAIL，第四卡片和操作区不存在。

- [ ] **Step 4: 更新 HTML 和 CSS**

在现有面板中：

- 标题阈值文案改为“实体 F1 90% · 链接可访问率 98% · 结论支撑准确率 90%”；
- 网格改为 `repeat(4, minmax(0, 1fr))`；
- 第二卡片同一数值区显示 `Recall xx.xx% · F1 xx.xx%`；
- 增加只接受 `.json,.xlsx` 的隐藏文件输入和可见上传按钮；
- 增加“重新测评”、Word/PDF 下载按钮；初始禁用；
- 状态颜色始终配套可见文字；窄屏仍为单列。

- [ ] **Step 5: 重写面板视图模型以适配稳定摘要**

`evaluation_panel.js` 的 `buildViewModel` 只读取：

```javascript
const entity = summary?.entity || {};
const accessibility = summary?.public_links?.accessibility || {};
const support = summary?.public_links?.claim_support || {};
const paths = summary?.evaluation_report_paths || {};
```

返回 `cards`、`rows`、`message`、`downloads`、`groundTruth`、`canRerun`。`render` 和 `reset` 同步清理四张卡、表格、文件名、按钮和下载链接。

- [ ] **Step 6: 在 `scripts.js` 接入上传与重新测评**

新增模块级状态：

```javascript
let currentEvaluationRunId = '';
let currentEvaluationTask = '';
```

自动生成成功时取 `data.run_statistics.run_id` 和 `data.run_statistics.task`。上传流程用 `FormData` 调用 `/api/evaluation-ground-truth`，成功后显示安全文件名和实体数量；重新测评调用 `/api/report-evaluation/${encodeURIComponent(currentEvaluationRunId)}`，渲染新摘要并更新下载按钮。

要求：

- 上传按钮忙碌时禁用；失败显示服务端安全消息；
- 重新测评不调用 `startResearch`；
- 开始新任务和加载不含摘要的旧历史时清空 run id、文件状态和下载链接；
- 下载路径通过现有静态 `/outputs` 机制，添加 `download` 属性；
- API 失败不清空当前研究报告。

- [ ] **Step 7: 运行 JS、静态和相关浏览器逻辑测试**

```powershell
node --check frontend/evaluation_panel.js
node --check frontend/scripts.js
node tests/js/check_evaluation_panel.cjs
& $python -m pytest tests/test_evaluation_dashboard_ui.py -q
```

Expected: 全部 PASS。

- [ ] **Step 8: 提交**

```powershell
git add frontend/index.html frontend/evaluation_panel.js frontend/scripts.js frontend/styles.css tests/js/check_evaluation_panel.cjs tests/test_evaluation_dashboard_ui.py
git commit -m "feat: add four metric evaluation controls"
```

## Task 10：端到端回归、真实导出和受控 UI 验收

**Files:**

- Modify: `tests/test_formal_pipeline.py`
- Modify: `tests/test_evaluation_pipeline.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-21-entity-and-source-evaluation-dashboard-design.md` only if implementation reveals a necessary factual correction

- [ ] **Step 1: 增加端到端契约测试**

用替换后的研究/写作/网络依赖运行一次完整服务流程，输入包含一条截图式免责句，断言：

- `result.report`、写入 MD 的文本、送给 Word/PDF 渲染器的文本都没有违规句；
- `report_style_cleanup.removed_count == 1`；
- 实体、可访问性、断言支撑结果来自同一清理后正文；
- `evaluation_summary.public_links` 有两个子指标；
- `evaluation_report_paths.word/pdf` 存在；
- 其中一个测评导出失败时原报告三个下载路径仍存在。

- [ ] **Step 2: 运行全部自动测试**

```powershell
& $python -m pytest -q
```

Expected: PASS，无新增 warning 被当作错误。

- [ ] **Step 3: 运行静态检查**

```powershell
node --check frontend/evaluation_panel.js
node --check frontend/scripts.js
node tests/js/check_evaluation_panel.cjs
& $python -m compileall backend gpt_researcher main.py three_agent_service.py
git diff --check
```

Expected: 全部 exit 0，`git diff --check` 无输出。

- [ ] **Step 4: 做计划覆盖和签名一致性自审**

Run:

```powershell
rg -n "clean_formal_report_style|report_style_cleanup|claim_support|evaluation_report_paths|evaluation-ground-truth|report-evaluation" backend gpt_researcher main.py three_agent_service.py frontend tests
rg -n "weighted_score|checked_support_accuracy|本报告不作|本报告不进行|本报告无法据此" backend gpt_researcher three_agent_service.py
rg -n "build_evaluation_summary\(" . -g "*.py"
```

Expected:

- 第一条覆盖实现和测试；
- 第二条不再出现旧加权评分，报告提示词可包含禁止表达说明，但导出流水线必须有确定性清理；
- 第三条所有调用者传参和新签名一致。

- [ ] **Step 5: 用真实渲染器生成一份小型受控测评报告**

使用测试夹具数据调用 `export_evaluation_report`，不访问公网；确认生成 `.docx` 和 `.pdf`。检查文件：

```powershell
Get-ChildItem -LiteralPath outputs/evaluations | Sort-Object LastWriteTime -Descending | Select-Object -First 3 Name,Length,LastWriteTime
```

Expected: Word/PDF 文件大小均大于 0。

- [ ] **Step 6: 在隔离端口启动并做 API/UI 冒烟**

Run:

```powershell
& $python -m uvicorn main:app --host 127.0.0.1 --port 8001
```

在另一个终端使用本地夹具：

1. 打开 `http://127.0.0.1:8001/`；
2. 确认测评面板默认隐藏；
3. 通过上传控件提交合法 JSON 和 XLSX；
4. 对测试记录点击重新测评；
5. 确认四卡片、分类表、状态文字及 Word/PDF 下载均正确；
6. 替换标准答案后重新测评，确认历史记录新增且不触发研究进度；
7. 确认窄屏四卡片单列且按钮可操作。

Expected: 无控制台异常；API 均返回预期状态；文件可下载。

- [ ] **Step 7: 更新 README 的用户操作说明**

增加简短章节，说明支持的 JSON/Excel 列、三类指标定义、三个阈值、上传/重新测评流程，以及无标准答案时代理指标不等于准确率。不得复制内部审计原文。

- [ ] **Step 8: 最终回归并提交**

```powershell
& $python -m pytest -q
node --check frontend/evaluation_panel.js
node --check frontend/scripts.js
node tests/js/check_evaluation_panel.cjs
git diff --check
git status --short
git add README.md tests/test_formal_pipeline.py tests/test_evaluation_pipeline.py docs/superpowers/specs/2026-09-21-entity-and-source-evaluation-dashboard-design.md
git commit -m "test: verify cleaned report evaluation workflow"
```

Expected: 所有验证通过；提交前 `git status --short` 只列出本任务预期文件。

## 完成前审查清单

- [ ] 六张截图对应句式均有回归测试且从最终 Markdown、Word、PDF 共用正文中删除。
- [ ] 正常的时间、样本量、来源范围说明未被误删。
- [ ] JSON 与 XLSX 使用同一校验器，错误包含安全、可定位的信息。
- [ ] 实体严格指标只在合法标准答案存在时显示。
- [ ] 可访问率按唯一 URL；支撑准确率按断言—链接关系；部分支撑不计入正确项。
- [ ] 自动生成和重新测评调用同一评分编排器。
- [ ] 重新测评不触发检索、写作或编辑模型。
- [ ] 页面、独立测评报告和历史记录使用同一稳定摘要。
- [ ] 任一测评分项或测评报告格式失败均不阻止原研究报告导出。
- [ ] 所有路径均由服务端生成且限制在 `outputs/records` 或 `outputs/evaluations`。
- [ ] 全量 pytest、Node 语法检查、JS 断言、编译检查和 `git diff --check` 均通过。
