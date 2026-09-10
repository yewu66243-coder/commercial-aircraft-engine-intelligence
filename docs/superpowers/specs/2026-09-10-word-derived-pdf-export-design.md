# Word 派生 PDF 导出设计

## 背景

当前正式报告的 Word 和 PDF 由同一份 Markdown 分别生成：Word 生成器会添加封面、动态目录、页眉和页码，PDF 生成器则使用 HTML/CSS 直接排版。因此同一份 `df74` 报告在 Word 中为 9 页，在 PDF 中为 6 页，并且 PDF 缺少独立封面和带页码的目录。正文内容大体相同，但结构、分页和版式不一致。

## 目标

- 正式报告先生成 DOCX，再从该 DOCX 导出 PDF。
- PDF 与用户打开的 Word 文件保持相同的封面、目录、图片位置、分页和页码。
- 导出 PDF 前更新 Word 的目录、正文域、页眉页脚域并重新分页。
- 未安装 Microsoft Word 或 Word 自动化失败时，保留 Markdown 和 DOCX，并明确将 PDF 标记为导出失败。
- 不再以 HTML/WeasyPrint 结果静默替代正式报告的 Word 派生 PDF。

## 非目标

- 不修改报告正文、引用内容或图片选择逻辑。
- 不重新设计现有 Word 模板和国标论文版式。
- 不要求无 Microsoft Word 的环境生成与 Word 完全一致的 PDF。
- 不移除现有的独立 Markdown 转 PDF 能力；它仅不再用于正式报告的成对 Word/PDF 导出。

## 方案选择

采用 Microsoft Word 原生自动化作为 Windows 正式报告的 PDF 渲染器。系统先写入 DOCX，然后用 Word 打开该文件，更新字段和目录、重新分页、保存 DOCX，最后调用 Word 的固定格式导出能力生成同名 PDF。

该方案优于让 HTML/CSS 模仿 Word，因为分页、字体度量、目录页码和浮动对象布局均由同一个 Word 排版引擎决定。它也优于删减 Word 版式去匹配现有 PDF，因为正式报告仍可保留封面、目录和规范页眉页码。

## 生成流程

1. 将最终 Markdown 保存为同名 `.md`。
2. 使用现有 `render_word` 生成同名 `.docx`。
3. 若 DOCX 生成成功，调用新增的 Word 到 PDF 转换函数。
4. 转换函数在独立线程中初始化 Windows COM，启动不可见的 Word 实例，并以非只读方式打开刚生成的 DOCX。
5. 更新正文域、目录域、页眉页脚域，重新分页并保存 DOCX。
6. 从当前文档原生导出同名 `.pdf`。
7. 关闭文档、退出 Word，并释放 COM 资源。
8. 返回 Markdown、DOCX 和 PDF 路径；三者使用相同文件名主体。

正式报告入口必须统一遵循上述顺序，包括 3-Agent Web 接口、传统报告接口和 CLI 中同时请求 Word/PDF 的路径。独立 PDF 调用可以继续使用现有 Markdown 到 PDF 功能，但不能作为正式报告转换失败时的回退。

## 错误处理

- DOCX 生成失败：不尝试 PDF 转换，Word 与 PDF 均标记失败。
- 非 Windows、未安装 Word、COM 初始化失败或 Word 导出失败：DOCX 保留，PDF 路径返回空字符串，导出状态明确为失败。
- 错误日志需区分“DOCX 生成失败”和“需要 Microsoft Word 才能导出同版 PDF”。
- 无论成功或失败，都必须在 `finally` 中关闭文档、退出 Word 并释放 COM，避免残留 `WINWORD.EXE` 进程。
- 不使用旧 HTML/WeasyPrint PDF 作为静默回退，以免再次产生版式不一致文件。

## 接口边界

- `render_word`：继续负责从 Markdown 创建 DOCX。
- 新增 Word 原生 PDF 渲染函数：只接收已有 DOCX 路径和目标 PDF 路径，不解析 Markdown。
- 异步文件写入层：负责在线程中执行 Word 转换、URL 路径编码和失败契约。
- 业务编排层：负责严格执行 Markdown → Word → PDF 的顺序，并汇总三种格式的导出状态。

## 测试与验收

1. 单元测试先证明正式报告在 DOCX 成功后才调用 Word 到 PDF 转换，并把实际 DOCX 路径传给转换函数。
2. 单元测试覆盖 DOCX 失败、Word 不可用和 PDF 转换异常，验证 PDF 路径为空且不会调用 HTML PDF 回退。
3. 在装有 Word 的 Windows 环境运行真实集成测试，验证 DOCX 与 PDF 均存在、页数一致且正文可提取。
4. 使用当前 `df74.docx` 重新导出 PDF，验收结果应为 9 页，并包含：
   - 第 1 页独立封面；
   - 第 3 页带正确页码的独立目录；
   - 正文、图片和参考文献与 Word 位于相同页面；
   - 页眉和页脚页码一致。
5. 运行现有正式导出和正式报告流水线测试，确保 Markdown、引用链接和 Word 生成能力无回归。
6. 重启本地服务并验证 Web 端新任务能够下载同版 Word 和 PDF。

## 兼容性与依赖

Windows 正式报告 PDF 导出依赖 Microsoft Word 和 `pywin32`。项目应声明仅 Windows 安装的 `pywin32` 依赖。没有 Microsoft Word 的机器仍能生成 Markdown 和 DOCX，但正式同版 PDF 会明确不可用。
