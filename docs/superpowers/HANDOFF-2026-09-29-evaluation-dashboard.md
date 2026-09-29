# 测评报告功能实施交接（2026-09-29）

## 1. 必须先读：当前真实状态

- 功能工作树：`C:\Users\吴烨\.codex\worktrees\entity-evaluation\commercial-aircraft-engine-intelligence`
- 原始主工作区：`C:\Users\吴烨\Documents\GitHub\commercial-aircraft-engine-intelligence`
- 当前分支：`codex/entity-evaluation-dashboard`
- 当前 HEAD：`7a0c902 feat: add evaluation upload and rerun APIs`
- 交接前工作树：干净（Task 9 没有落盘任何半成品）。
- Python：`C:\Users\吴烨\Documents\GitHub\commercial-aircraft-engine-intelligence\.venv\Scripts\python.exe`
- 实施计划：`docs/superpowers/plans/2026-09-22-formal-report-cleanup-and-evaluation-exports.md`
- 设计文档：`docs/superpowers/specs/2026-09-21-entity-and-source-evaluation-dashboard-design.md`

不要在原始主工作区继续修改；后续命令的工作目录必须是上面的功能工作树。

### 当前唯一已确认的代码阻塞项

Task 7 的规格审查未通过：

- `gpt_researcher/evaluation/report_evaluation.py` 中 `evaluate_saved_report()` 直接执行 `del ground_truth_path`，实体测评仍调用 `evaluate_report_entities(report, task)`。
- 因此 API/调用方传入的“当前活动标准答案路径”不会真正生效；指定非默认目录或替换后的文件时，可能静默进入代理模式或使用错误文件。
- 现有测试还把错误调用固定为 `assert_called_once_with(REPORT, "GTF")`。
- 下一步必须先用 TDD 修复：实体评估明确读取/使用传入的 `ground_truth_path`；增加“默认目录另有文件时仍优先使用指定路径”的测试，并明确 `None` 时的代理模式行为。

Task 7 的其他规格项已通过。Task 8 已实现并提交，但尚未做独立规格/质量复审。

## 2. 用户最终目标

1. 删除正式报告中截图标黄的、类似“本报告不作判断/推断/结论”的免责声明式语句，同时保留正常事实、引用、表格和 Markdown 结构。
2. 增加测评能力，重点衡量：
   - 机构、参数、材料等核心技术实体抽取 Precision、Recall、F1；
   - 公开链接可访问率；
   - 公开信息对报告结论的断言—链接支撑准确率。
3. 支持 JSON/XLSX 标准答案上传。
4. 支持对已保存报告重新测评，且不重新触发研究、检索、写作或编辑模型。
5. 自动生成独立 Markdown、Word、PDF 测评报告。
6. 前端显示四张测评卡片，并提供标准答案上传、重新测评和 Word/PDF 下载控件。

用户已明确要求“子代理分任务实施”。前序执行方式是：实现代理 → 规格审查代理 → 质量审查代理；发现问题后交回实现代理 TDD 修复，再复审。

## 3. 已完成且审查通过

### Task 1：JSON/XLSX 标准答案输入层

状态：规格与质量审查均通过。

主要提交：

- `e1e405e feat: accept json and xlsx entity ground truth`
- `6ffac10`、`9058ddf`、`515627b`、`82caf5b`、`edc05dd`：输入校验、安全、资源限制和 worksheet 关系修复。

实现要点：统一 JSON/XLSX 解析校验、原子持久化、哈希安全路径、资源限制、旧适配器兼容。

### Task 2：正式报告免责声明确定性清理

状态：规格与质量审查均通过。

主要提交：`fb8aeac` 至 `1729761`。

实现要点：覆盖六张截图句式；按主语与子句保守删除；保护代码、链接、表格和引用；线性扫描；不误删事实内容。

### Task 3：断言—链接关系支撑评分

状态：规格与质量审查均通过。

主要提交：`fc9ffa6` 至 `874b71c`。

实现要点：以断言—URL 关系为分母，只有 `supported` 计入分子；唯一 URL 只抓取一次；安全解析来源编号；覆盖表格断言；排除证据目录引用。

### Task 4：可复用链接可访问性评估器

状态：规格与质量审查均通过。

主要提交：`d175528` 至 `ea9617c`。

实现要点：唯一 URL 可访问性、IPv6/IDN、凭据 URL 拒绝/脱敏、HTTP 错误关闭、GFM 管道与非法百分号处理、注入 checker 前置检查。

### Task 5：稳定嵌套测评摘要

状态：规格与质量审查均通过。

主要提交：`ac51b2d` 至 `b5d7c51`。

实现要点：`entity`、`public_links.accessibility`、`public_links.claim_support` 嵌套结构；保留兼容扁平别名；安全白名单明细；派生比率/数量一致；路径安全。

### Task 6：独立 Markdown/Word/PDF 测评报告

状态：最终规格与质量审查通过。

主要提交：

- `973084c feat: export standalone evaluation reports`
- `2e4b21f`、`485bb33`、`3f7ce43`：凭据脱敏。
- `99ceca3 fix: harden evaluation report export safety`
- `7ba0b04 fix: preserve evaluation export paths`

最终验证：`tests/test_evaluation_report.py` + `tests/test_evaluation_summary.py` 共 66 项通过。

实现要点：

- 任意 Authorization/Bearer 凭据和编码凭据脱敏；普通 bearer 叙述保留。
- 动态字段按纯文本转义，禁止 Markdown/HTML 链接、粗体和图片注入。
- 文件名限制为 URL 可安全往返字符；点号任务名和 `%2F` URL 正确保留。
- 独占锁、临时渲染、原子不覆盖发布；取消后清理；单格式失败相互隔离。
- 明细每类最多 500 行，并显示截断说明。

## 4. 已提交但仍需审查/修复

### Task 7：测评记录与统一重评编排

提交：`a0b3ae4 feat: orchestrate report evaluation records`

涉及文件：

- `gpt_researcher/evaluation/records.py`
- `gpt_researcher/evaluation/report_evaluation.py`
- `gpt_researcher/evaluation/__init__.py`
- `three_agent_service.py`
- `tests/test_evaluation_records.py`
- `tests/test_report_reevaluation.py`
- `tests/test_evaluation_pipeline.py`
- `tests/test_formal_pipeline.py`
- `tests/test_report_finalization.py`

已实现：原子记录写入、损坏备份、进程内锁、重评历史追加；三类评估器故障隔离；clean → evaluate → 原报告格式化/导出顺序；`record_version = 3.0.0`；测评失败不阻塞原报告导出。

已有验证：

- Task 7 隔离回归：77 passed，另有 1 个真实导出环境测试失败。
- API 联调：13 passed。
- 扩展评估/报告/清理回归：135 passed。

必须先修上文“ground_truth_path 被丢弃”问题，然后重新进行 Task 7 规格审查和质量审查。

建议首轮测试：

```powershell
& 'C:\Users\吴烨\Documents\GitHub\commercial-aircraft-engine-intelligence\.venv\Scripts\python.exe' -m pytest tests/test_evaluation_records.py tests/test_report_reevaluation.py tests/test_evaluation_pipeline.py tests/test_formal_pipeline.py tests/test_report_finalization.py -q
```

### Task 8：上传与重评 API

提交：`7a0c902 feat: add evaluation upload and rerun APIs`

涉及文件：

- `main.py`
- `tests/test_evaluation_api.py`

已实现：

- `POST /api/evaluation-ground-truth`：JSON/XLSX、5 MiB 限制、安全 413/422、哈希文件名、原子替换。
- `POST /api/report-evaluation/{run_id}`：未知记录 404、缺正文/任务 409、当前活动标准答案、局部失败仍 200、追加历史，不调用研究/模型。

已有验证：70 项中 68 passed、2 skipped（环境相关）；单独 API 联调 13 passed；`git diff --check` 通过。

注意：Task 8 依赖 Task 7 的 `ground_truth_path` 语义，因此必须在 Task 7 修复后重跑 API 测试。随后依次做 Task 8 规格审查、质量审查；目前不能宣称 Task 8 最终批准。

建议测试：

```powershell
& 'C:\Users\吴烨\Documents\GitHub\commercial-aircraft-engine-intelligence\.venv\Scripts\python.exe' -m pytest tests/test_evaluation_api.py tests/test_security_fix.py tests/test_model_provider_selection.py -q
```

## 5. 尚未实施

### Task 9：前端四卡片与操作区

状态：代理仅完成阅读和定位，补丁未落盘，测试未运行，工作树无 Task 9 修改。

需要修改：

- `frontend/index.html`
- `frontend/evaluation_panel.js`
- `frontend/scripts.js`
- `frontend/styles.css`
- `tests/js/check_evaluation_panel.cjs`
- `tests/test_evaluation_dashboard_ui.py`

核心要求：

- 四卡顺序：实体 Precision；实体 Recall/F1；链接可访问率；结论支撑准确率。
- 严格模式显示比例和达标状态；代理模式显示“待标准答案”并单列代理证据支撑率；`null` 为 `—`；无链接显示“无公开链接”。
- 增加 `.json,.xlsx` 上传、重新测评、Word/PDF 下载按钮和 ARIA live 状态。
- 使用嵌套摘要为主，兼容旧字段。
- 重新测评只调用 `/api/report-evaluation/{run_id}`，不得调用 `startResearch`。
- 新任务和无摘要旧历史必须清空 run id、文件状态、下载链接。

### Task 10：完整回归、真实导出、UI 冒烟、README

状态：未开始。

需要完成：

- 端到端契约测试：六类免责声明从统一最终正文删除；测评与三种原报告格式使用同一清理后正文；单个测评格式失败不影响原报告三个下载文件。
- 全量 pytest、Node 语法、JS 断言、compileall、`git diff --check`。
- 使用真实渲染器生成受控测评报告。
- 在隔离端口（建议 8001）做上传、重评、四卡、下载、替换标准答案和窄屏 UI 冒烟。
- 更新 README：JSON/Excel 列、指标定义、阈值、上传/重评流程、代理指标不等于准确率。
- 最终综合代码审查与完成前验证。

## 6. 已知环境限制

真实 Word/PDF 导出测试在当前 Windows 环境存在外部依赖问题：

- WeasyPrint 缺少 `libgobject-2.0-0`。
- Word COM 曾返回 `0x800706ba`。

这些不是已发现的业务逻辑回归，但最终不能把相关测试宣称为通过。应优先使用 mocked 单元/集成测试完成逻辑验证；Task 10 再单独处理真实渲染环境或明确记录为环境阻塞。

## 7. 推荐续接顺序

1. 在功能工作树确认 `git status --short` 干净、HEAD 为 `7a0c902`。
2. 新实现代理 TDD 修复 Task 7 的 `ground_truth_path` 传递/加载语义，提交小修复。
3. 原规格审查思路复验 Task 7；通过后做 Task 7 质量审查。
4. 重跑 Task 8 API/安全测试；做 Task 8 规格审查和质量审查，修复所有阻塞问题。
5. 实施 Task 9，随后规格审查、质量审查。
6. 实施 Task 10；运行全量自动验证、真实导出和 UI 冒烟。
7. 使用 `verification-before-completion` 做证据化最终检查，再使用 `finishing-a-development-branch` 向用户提供合并/PR/保留分支选项。未经用户选择不要合并。

## 8. 快速状态命令

```powershell
Set-Location -LiteralPath 'C:\Users\吴烨\.codex\worktrees\entity-evaluation\commercial-aircraft-engine-intelligence'
git status --short
git branch --show-current
git log --oneline -12

& 'C:\Users\吴烨\Documents\GitHub\commercial-aircraft-engine-intelligence\.venv\Scripts\python.exe' -m pytest tests/test_report_reevaluation.py tests/test_evaluation_api.py -q
node --check frontend/evaluation_panel.js
node --check frontend/scripts.js
node tests/js/check_evaluation_panel.cjs
git diff --check
```

## 9. 关键安全/行为不变量

- 上传文件名不得直接进入服务器路径；只保存服务端生成的安全哈希名。
- 公开测评报告不得包含凭据、查询串、内部异常、内部路径或完整原始评估对象。
- 实体严格指标只在合法标准答案存在时显示；代理指标必须明确标为代理，不能冒充准确率。
- 可访问率按唯一 URL；结论支撑率按断言—URL 关系；`partial` 不计入正确项。
- 自动生成与重评必须走同一编排器和同一稳定摘要。
- 重评不得触发检索、写作、编辑或其他生成模型。
- 任一测评分项、Word 或 PDF 测评导出失败不得阻止原报告导出。
- 所有下载路径必须由服务端生成并限制在 `outputs/records` 或 `outputs/evaluations`。
- 不覆盖既有历史结果；并发与取消时不能留下可见半成品。
