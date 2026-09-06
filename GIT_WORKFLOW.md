# 商用航空发动机情报工作台 Git 管理规范

## 分支

- `main`：保存经过验证、可运行的稳定版本。
- `feature/<名称>`：新增功能，例如 `feature/qwen-model-selector`。
- `fix/<名称>`：修复问题，例如 `fix/report-image-layout`。
- `docs/<名称>`：仅修改说明文档。

一个功能或修复使用一个分支。验证通过后再合并到 `main`。

## 日常流程

```powershell
git switch main
git switch -c feature/功能名称

# 修改后检查
git status --short
git diff

# 只添加本次相关文件
git add 文件或目录
git diff --cached
git commit -m "feat: 简要说明功能"

# 合并稳定版本
git switch main
git merge --no-ff feature/功能名称
```

提交类型建议使用：`feat`（功能）、`fix`（修复）、`docs`（文档）、`test`（测试）、`refactor`（重构）、`chore`（维护）。一次提交只表达一个完整变更。

## 提交前检查

```powershell
git status --short
git diff --check
git diff --cached
```

涉及报告生成代码时，运行项目的报告和模型选择回归测试，并检查前端脚本语法。提交后用 `git log --oneline --decorate --graph -10` 确认历史。

## 不进入版本库的内容

以下内容由 `.gitignore` 排除：

- `.env` 和本机 MCP 连接配置。
- `local_docs/` 中的论文、专利和用户资料。
- `outputs/` 中的报告、图片和运行记录。
- `portable_python/`、`dependencies/` 和 `ollama_models/` 等本机运行文件。
- `temp_docs/`、日志、缓存和构建结果。

这些文件需要使用独立的安全备份。`.env.example` 可以提交，但只能保留变量名和示例值。

## 版本与恢复

稳定节点使用带说明的标签：

```powershell
git tag -a v1.0.0 -m "稳定版本说明"
git tag --list
```

恢复尚未提交的单个文件使用 `git restore 文件路径`。撤销已经提交且可能已共享的改动使用 `git revert 提交编号`，保留完整历史。

## 远程仓库

项目可能涉及内部研究资料，远程仓库应设置为私有。添加远程地址后执行：

```powershell
git remote add origin 远程仓库地址
git push -u origin main
git push origin --tags
```
