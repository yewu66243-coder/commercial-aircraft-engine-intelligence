# Windows 便携运行环境

本目录提供商用航空发动机情报工作台的 Windows x64 便携运行环境。

## 安装

1. 安装 Git LFS，并运行 `git lfs install`。
2. 克隆本仓库；Git LFS 会自动下载运行环境压缩包。
3. 双击 `runtime\install_runtime.bat`，将环境解压到项目根目录。
4. 将 `.env.example` 复制为 `.env`，配置 DeepSeek 的 `OPENAI_API_KEY`，或配置千问的 `DASHSCOPE_API_KEY`。
5. 双击项目根目录的 `start_system.bat`。
6. 浏览器访问 `http://127.0.0.1:8000/`。

## 内容

- Python 3.11.9 便携运行环境及项目依赖。
- GTK/WeasyPrint 所需的 Windows 动态库。
- 压缩包：`commercial-aircraft-engine-runtime-win-x64-py311.zip`
- SHA-256：`70f340520f12f83557f464a5cf3f8000aefba7dc6f285a5619c74f15224345b8`

压缩包不包含 `.env`、API 密钥、本地论文与专利资料、生成报告或个人配置。本地 Ollama 模型体积约 3.86 GB，也未包含；使用 DeepSeek 或千问时不需要该模型，嵌入服务不可用时系统会使用关键词检索兜底。
