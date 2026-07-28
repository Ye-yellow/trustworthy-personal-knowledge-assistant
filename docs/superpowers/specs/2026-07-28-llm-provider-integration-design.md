# L0 LLM Provider 与 sub2api 接入设计

> 状态：设计已确认，等待书面规格复核
> 日期：2026-07-28
> 范围：P0 的 L0 工程基线与统一模型接入层

## 1. 决策

模型接入采用 LangChain 统一模型接口，不自建完整 Provider SDK。业务模块不直接实例化 `ChatOpenAI` 或其他具体模型类，而是依赖项目内的薄层 `ModelGateway` 和按用途选择模型的 `ModelRouter`。

P0 默认模型配置为：

| 项目 | 默认值 |
|---|---|
| 逻辑 Provider | `sub2api` |
| LangChain Provider | `openai` |
| Base URL | `http://localhost:8080/v1` |
| 模型 | `gpt-5.5` |
| 传输协议 | OpenAI Chat Completions |
| 密钥来源 | `TRUSTKB_LLM_API_KEY` 环境变量 |

当前 WSL sub2api 已通过 `/v1/models` 鉴权和 `gpt-5.5` Chat Completions 合成烟测。真实 key 不进入仓库、配置样例、日志、测试快照或异常信息。

## 2. 选择 LangChain 的原因

- `init_chat_model` 为不同 Provider 提供一致的调用、流式输出和结构化输出接口。
- sub2api 实现 OpenAI-compatible API，可以通过 `model_provider="openai"`、自定义 `base_url` 和 `api_key` 接入。
- 后续接入 Ollama、官方 OpenAI、Anthropic 或其他 LangChain Provider 时，业务调用方式不变。
- LangGraph 可以直接消费 LangChain Chat Model，减少后续工作流适配代码。

本项目只依赖标准消息、结构化输出、流式输出和 usage metadata。任何第三方网关的非标准响应字段都不进入领域接口。

## 3. 模块边界

建议首批目录：

```text
src/trustworthy_kb/
  __init__.py
  config/
    __init__.py
    settings.py
  llm/
    __init__.py
    errors.py
    factory.py
    gateway.py
    router.py
    types.py
tests/
  config/
  llm/
  integration/
```

### 3.1 `settings.py`

负责从环境变量加载并校验模型配置。API key 使用 Pydantic `SecretStr`，对象的 `repr`、日志和校验错误不能包含明文。

支持以下配置：

- `TRUSTKB_LLM_PROVIDER`
- `TRUSTKB_LLM_MODEL`
- `TRUSTKB_LLM_BASE_URL`
- `TRUSTKB_LLM_API_KEY`
- `TRUSTKB_LLM_TIMEOUT_SECONDS`
- `TRUSTKB_LLM_MAX_RETRIES`
- `TRUSTKB_LLM_EXTRACTOR_MODEL`
- `TRUSTKB_LLM_VERIFIER_MODEL`
- `TRUSTKB_LLM_CURATION_MODEL`
- `TRUSTKB_LLM_ANSWER_MODEL`

四个用途模型覆盖项均可省略；省略时使用 `TRUSTKB_LLM_MODEL=gpt-5.5`。

### 3.2 `factory.py`

负责把项目配置映射为 LangChain 模型。sub2api 使用 `init_chat_model` 的 OpenAI Provider、自定义 Base URL、显式 API key、超时和有限重试。当前明确使用 Chat Completions，不自动切换 Responses API。

工厂只认识配置和 LangChain，不包含 Claim、证据或问答业务逻辑。

### 3.3 `router.py`

定义 `ModelPurpose`：

- `CLAIM_EXTRACTION`
- `EVIDENCE_VERIFICATION`
- `CURATION`
- `ANSWER_GENERATION`

路由器按用途解析模型名，并通过工厂创建或复用 LangChain Chat Model。P0 所有用途默认使用 `gpt-5.5`，但配置层允许独立覆盖，不在业务节点中硬编码模型。

### 3.4 `gateway.py`

向业务层暴露稳定的调用入口：普通调用、结构化调用和流式调用。输入使用 LangChain 标准消息类型；sub2api 的结构化调用使用 LangChain `with_structured_output(..., method="json_mode")`，并必须通过 Pydantic Schema 校验，不接受未校验文本作为成功结果。

Gateway 的职责仅包括：

- 选择用途模型；
- 添加调用级 metadata 和 tags；
- 将 LangChain/HTTP 异常映射为项目异常；
- 提取标准文本、usage 和 finish reason；
- 确保异常和日志脱敏。

Gateway 不负责提示词拼接、证据判断、自动重试工作流或业务降级。

## 4. 数据流

```text
业务节点
  → ModelGateway
  → ModelRouter(ModelPurpose)
  → LangChain init_chat_model
  → sub2api OpenAI-compatible /chat/completions
  → LangChain AIMessage
  → 项目标准响应或 Pydantic 结构化结果
```

模型用途、逻辑 Provider、模型名和 prompt version 必须作为 metadata 记录；API key、完整私人正文和未脱敏响应不能进入日志。

## 5. 失败策略

定义以下项目异常：

- `ModelConfigurationError`
- `ModelAuthenticationError`
- `ModelTimeoutError`
- `ModelRateLimitError`
- `ModelProviderError`
- `ModelOutputValidationError`

异常消息只能包含 Provider、模型、错误类别、HTTP 状态和 request ID，不包含 Authorization Header、API key、请求正文或原始响应正文。

模型失败时禁止静默切换到 Fake Model 或让模型凭训练记忆补答。后续工作流根据用途执行安全失败：

- Claim 抽取失败：保留 SourceVersion，任务进入可重试失败状态。
- 证据验证失败：Claim 进入 `INSUFFICIENT` 或人工 Review，不得激活。
- 回答生成失败：返回系统不可用，不降级为无证据答案。

## 6. 密钥与隐私

- 应用只读取 `TRUSTKB_LLM_API_KEY`，不直接访问 sub2api 数据库或 OpenClaw 配置。
- `.env.example` 只包含空占位符；真实 `.env` 已由 `.gitignore` 排除。
- 自动化测试不得读取真实 key。
- sub2api 连通性测试必须显式启用，并通过当前进程环境注入 key，不写入文件。
- 日志测试必须验证 `SecretStr`、Header、异常和配置对象不会泄露 key。

## 7. 测试设计

### Unit

- Settings 默认值、用途覆盖和非法配置。
- SecretStr 的 `repr` 与校验错误脱敏。
- Factory 向 LangChain 传递正确的 Provider、模型、Base URL、超时和重试参数。
- Router 为四种用途选择正确模型并复用实例。
- Gateway 普通、结构化和流式调用。
- LangChain 异常到项目异常的稳定映射。
- 日志和异常中不存在测试 key。

### Integration

- 使用 LangChain Fake Chat Model 验证 Gateway，不访问网络。
- 使用 Mock HTTP 验证 OpenAI-compatible 请求结构、401、429、5xx、超时和无效 JSON。
- 显式 smoke 标记调用本机 sub2api `/v1/models` 和 `gpt-5.5` 合成请求；默认 CI 跳过。

### 质量门

- 所有公共函数均有测试。
- L0 新增代码覆盖率不低于 80%。
- Ruff、mypy、pytest 和隐私扫描全部通过。
- 默认测试不依赖 WSL、Docker、网络或真实凭据。

## 8. L0 同批工程基线

本设计落地时同时建立：

- `pyproject.toml` 与锁文件；
- `src/` 布局和显式 `__all__`；
- pytest、pytest-asyncio、coverage、Ruff 和 mypy；
- GitHub Actions 的 lint、type-check、unit test 和 coverage；
- `.env.example` 的安全占位配置；
- 公开仓库隐私扫描。

L0 不实现 SQLite Schema、Obsidian Adapter、LangGraph 工作流、Milvus 或业务 Prompt。这些内容按已批准的基础设施分层顺序进入后续层。

## 9. 验收标准

1. 业务测试可以通过 Fake Model 调用 Gateway，而不依赖具体 Provider。
2. 默认配置能解析为 `sub2api/gpt-5.5`，四种用途可独立覆盖模型。
3. LangChain 通过自定义 Base URL 调用 OpenAI-compatible Chat Completions。
4. 结构化输出必须通过 Pydantic 校验，失败时返回脱敏项目异常。
5. 缺少或错误 key 时安全失败，不输出凭据。
6. 所有默认测试离线通过，显式 smoke test 可以连接当前 WSL sub2api。
7. 新增代码覆盖率不低于 80%，CI 和隐私扫描通过。
