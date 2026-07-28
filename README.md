# Trustworthy Personal Knowledge Assistant

一个以 Obsidian 为人工可读知识真源、以 SQLite 管理知识血缘和状态、以 Milvus 提供可重建混合检索的可信个人知识助手。

项目已完成 L0 工程/模型网关基线和 L1 领域/SQLite 控制面。采集、知识治理、
Obsidian 发布与检索仍未实现，因此当前版本尚不适合保存或处理真实私人资料。

## 核心原则

- 原始资料保留不可变版本，AI 不静默覆盖原文。
- Claim、证据、质量裁决、整理稿、索引和答案保持可追溯血缘。
- 未验证、冲突、过期或疑似污染的内容不能进入默认问答域。
- Obsidian 是人工可读知识真源；Milvus、缓存和模型输出均可重建。
- 证据不足时明确拒答，不使用模型训练记忆补全事实。

## 技术方向

- Python 3.12+
- FastAPI、Pydantic v2、SSE
- LangGraph、LangChain
- SQLite、Milvus Standalone
- BGE-M3、BGE-Reranker-v2-m3、BM25、RRF
- pytest、Golden Dataset、RAGAS

## 开发顺序

项目采用基础设施分层推进：

1. L0：工程与安全基线
2. L1：领域模型与 SQLite 控制面
3. L2：采集、Obsidian Adapter 与运行恢复
4. L3：Claim、证据与质量治理
5. L4：发布、Milvus 混合检索与对账
6. L5：API、可信问答与评估

完整架构和验收定义见[设计规格](docs/superpowers/specs/2026-07-28-trustworthy-personal-knowledge-assistant-design.md)。

## 当前可用：统一模型网关

业务代码通过 `ModelGateway` 和 `ModelRouter` 调用 LangChain，不直接依赖
`ChatOpenAI`、`ChatAnthropic` 或 `ChatOllama`。默认配置为本机 WSL sub2api 的
OpenAI-compatible Chat Completions：

```powershell
uv sync --extra dev
$env:TRUSTKB_LLM_API_KEY = "<your-local-sub2api-key>"
uv run python examples/chat.py
```

四个业务用途可分别覆盖模型；未设置时均继承 `TRUSTKB_LLM_MODEL=gpt-5.5`：

- `TRUSTKB_LLM_EXTRACTOR_MODEL`
- `TRUSTKB_LLM_VERIFIER_MODEL`
- `TRUSTKB_LLM_CURATION_MODEL`
- `TRUSTKB_LLM_ANSWER_MODEL`

Provider 切换只修改环境配置，业务调用代码不变：

| 方式 | 安装 | 关键配置 |
| --- | --- | --- |
| WSL sub2api（默认） | `uv sync` | `PROVIDER=sub2api`、本机 Base URL、key |
| OpenAI 官方 | 已包含 | `PROVIDER=openai`、模型、key，并清除自定义 Base URL |
| Anthropic | `uv sync --extra anthropic` | `PROVIDER=anthropic`、Claude 模型、key |
| Ollama 本地 | `uv sync --extra ollama` | `PROVIDER=ollama`、本地模型；无需 key |

表中的配置名均需加 `TRUSTKB_LLM_` 前缀。可复制 `.env.example` 作为字段清单，
但不要把真实值写回或提交该样例文件。详细边界见
[模型接入设计](docs/superpowers/specs/2026-07-28-llm-provider-integration-design.md)。

## 当前可用：L1 SQLite 控制面

L1 提供类型化 ULID、冻结领域记录、显式状态机、18 张控制面表、Alembic 迁移、
四组异步 Repository、append-only 审计哈希链、幂等租约协议和显式提交的
`SqliteUnitOfWork`。业务代码不直接消费 ORM Table，也不能由 Repository 隐式提交。

首次初始化或拉取新 migration 后执行：

```powershell
uv run alembic upgrade head
```

应用启动应调用 `assert_schema_current()` 检查数据库 revision；版本不一致会安全失败，
不会在运行时调用 `metadata.create_all()` 或静默迁移。默认数据库为
`./data/trustworthy_kb.db`，可通过 `TRUSTKB_DATABASE_URL` 覆盖，但只接受本地
`sqlite+aiosqlite` URL。

写入必须位于 Unit of Work 中并显式提交：

```python
async with unit_of_work_factory() as unit_of_work:
    source = await unit_of_work.sources.add_source(source_record)
    await unit_of_work.audit.append_operation_log(operation_log)
    await unit_of_work.commit()
```

完整表结构、不变量、并发和幂等约定见
[L1 设计规格](docs/superpowers/specs/2026-07-28-l1-domain-sqlite-control-plane-design.md)。

## 开发与验证

```powershell
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -m "not integration" --cov=trustworthy_kb
uv run python scripts/check_public_repository.py
uv run alembic upgrade head
```

真实 sub2api 测试默认跳过。只有在当前进程已安全注入 key 后，才显式设置
`TRUSTKB_RUN_SUB2API_INTEGRATION=1` 并运行
`uv run pytest tests/integration/test_sub2api.py`。测试只发送合成内容。

## 公开仓库隐私规则

本仓库只接受源代码、通用配置、架构文档和合成测试数据。以下内容不得提交：

- 真实 Obsidian Vault、个人笔记和附件
- `.env`、Token、密钥、证书和连接凭据
- SQLite 数据库、Milvus 数据、索引和模型缓存
- 包含私人内容的日志、Trace、截图、评估集和故障样本
- 用户名、本机绝对路径、私人 URL 或其他可识别信息

首次推送及后续发布前必须检查暂存区和提交历史。无法确认是否安全的内容默认不公开。

## License

尚未选择开源许可证。在许可证明确前，本仓库内容不授予复制、修改或再分发许可。
