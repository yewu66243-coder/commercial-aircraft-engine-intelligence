# DeepSeek / 千问双模型选择设计

## 目标

在商用航空发动机情报工作台的任务配置区增加生成大模型选择。DeepSeek 保持默认值；用户选择千问后，同一任务的分题研究、综合撰写、内容补充、成稿校订和来源复查全部使用千问。最终响应和后台研究记录保存实际使用的服务商及模型名。

## 采用方案

后端建立请求级模型运行配置，支持 `deepseek` 和 `qwen` 两个稳定标识。DeepSeek 沿用当前 OpenAI 兼容配置；千问使用阿里云百炼 OpenAI 兼容接口，默认模型为 `qwen-plus`，默认地址为 `https://dashscope.aliyuncs.com/compatible-mode/v1`。

每个 `ThreeAgentService` 实例只保存本任务的密钥、接口地址和模型名。研究器通过实例配置接收这些值，综合写作和编辑复查从同一实例创建客户端。实现不在任务执行期间改写进程环境变量，避免并发任务选择不同模型时互相影响。

## 配置与接口

DeepSeek 读取现有 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `FAST_LLM`、`SMART_LLM`、`STRATEGIC_LLM`，同时允许更明确的 `DEEPSEEK_*` 变量覆盖。千问读取：

- `DASHSCOPE_API_KEY`：必需的阿里云百炼 API Key。
- `QWEN_BASE_URL`：可选，缺省使用北京地域兼容接口。
- `QWEN_MODEL`：可选，缺省为 `qwen-plus`。
- `QWEN_FAST_MODEL`、`QWEN_STRATEGIC_MODEL`：可选，未配置时均使用 `QWEN_MODEL`。

新增 `GET /api/model-providers`，只返回模型名称、模型标识和是否已配置，不返回 API Key。报告请求新增 `llm_provider` 字段；未传时使用 `deepseek`。未知标识或选择了尚未配置的千问时返回 HTTP 400 和可操作的中文提示。

## 前端行为

任务配置区显示“生成大模型”下拉框，包含 DeepSeek 和千问。页面加载时读取服务商目录，展示当前模型名和配置状态。选中未配置的千问时保留选项并给出配置提示，启动按钮不会发起一个注定失败的长任务。请求体和旧 WebSocket 路径都携带 `llm_provider`。

状态面板显示当前任务选中的模型。后端返回的 `model_provider` 和后台 `run_statistics.model_provider` 用于确认实际运行模型。

## 兼容性与验收

未选择模型的旧请求继续使用 DeepSeek。现有报告结构、引用核验、图片处理、三轮校订和三格式导出不改变。

验收覆盖：服务商目录不泄露密钥；千问默认端点和模型解析正确；显式请求级地址优先于全局 DeepSeek 地址；研究器、写作器与编辑器共享所选运行配置；前端具有两个选项并发送正确字段；真实服务重启后目录接口和页面选择可用。
