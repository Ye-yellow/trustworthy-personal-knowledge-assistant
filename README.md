# Trustworthy Personal Knowledge Assistant

一个以 Obsidian 为人工可读知识真源、以 SQLite 管理知识血缘和状态、以 Milvus 提供可重建混合检索的可信个人知识助手。

项目已完成 L0 工程/模型网关基线、L1 领域/SQLite 控制面、L2 本地 Markdown
采集与故障恢复、L3 Claim/证据/质量治理，以及 L4 安全发布、Milvus 混合检索与三方对账。
L5 已提供仅监听回环地址的可信问答 JSON/SSE API、独立引用验证、确定性 Golden 门禁和
显式 RAGAS 适配。当前版本仍不适合处理任何数据的唯一副本；正式使用前应先完成本机合成
闭环验收，并为自己的知识域补充 Golden Dataset。

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

L1/L3/L5 提供类型化 ULID、冻结领域记录、显式状态机、22 张控制面表、Alembic 迁移、
六组异步 Repository、append-only 审计哈希链、幂等租约协议和显式提交的
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

## 当前可用：L2 Obsidian 采集与恢复

L2 提供手动全量扫描、官方 Obsidian CLI 只读 inventory、稳定双读、内容寻址快照、
Markdown 结构解析、安全信号、确定性变化规划、逐项原子应用，以及独立 SQLite
checkpoint 支持的 LangGraph 故障恢复。采集过程不调用 LLM 或网络服务。

运行前需要安装并启用 Obsidian 1.12.7+ 官方 CLI，然后显式配置一个本地 Vault。
真实 Vault ID、路径、数据库和快照均只能保存在本地环境，不能写入或提交仓库：

```powershell
uv sync --extra dev
$env:TRUSTKB_INGESTION_VAULT_ID = "<your-vault-id>"
$env:TRUSTKB_INGESTION_VAULT_PATH = "<absolute-path-to-your-vault>"
$env:TRUSTKB_INGESTION_ALLOWED_ROOTS = '["."]'
uv run alembic upgrade head
uv run trustworthy-kb-ingest
```

默认快照写入 `./storage/source-snapshots`，恢复 checkpoint 写入
`./storage/checkpoints/ingestion.sqlite`；两个目录都已被 Git 忽略。命令只输出 run ID、
状态和计数，不输出 Vault 路径或笔记正文。若进程在应用阶段中断，再次恢复同一
checkpoint thread 时会依据 SQLite 采集账本跳过已完成项，避免重复版本和审计记录。

首版只读取 `.md`，不修改 Vault；附件、监听模式、写回与向量索引均属于
后续层级。完整契约见
[L2 设计规格](docs/superpowers/specs/2026-07-28-l2-ingestion-recovery-design.md)。

## 当前可用：L3 Claim、独立证据与质量治理

L3 消费 L2 产生的 `KnowledgeChange`，完成结构化 Claim 提取、精确指纹去重、双向候选
搜索、独立 HTTPS 获取、内容寻址证据包、逐候选引用锁定验证、确定性质量策略和人工复核。
搜索使用 provider-neutral `EvidenceSearchGateway`；P0 默认通过 sub2api 的 Responses
`web_search`，以后可注册其他实现而不改业务编排。

模型返回的搜索摘要永远不作为证据。每个候选 URL 都必须经过 HTTPS/公网 DNS/连接对端
复核、robots、重定向、MIME 和字节预算检查，再独立下载、哈希和快照。T1/T2 只能来自
显式域名配置，未知域名默认 T4。模型只判断冻结证据候选的 stance、覆盖率和范围匹配；
最终 verdict 完全由代码策略产生。高风险、证据不足和冲突结果进入人工复核，只有低风险
结果允许自动裁决。

本地 `.env` 会被 CLI 自动读取且已被 Git 忽略。先初始化迁移，然后运行所有待处理变化：

```powershell
uv run alembic upgrade head
uv run trustworthy-kb-governance run
uv run trustworthy-kb-governance review list
uv run trustworthy-kb-governance review approve <review_request_id> --reason HUMAN_CONFIRMED
```

也可用 `run --change-id <knowledge_change_id>` 只处理一个变化。原始网页、提取文本、搜索
manifest 和 EvidencePack 保存在 `./storage/evidence-snapshots`，LangGraph 只把 ID、状态和
计数写入独立 `./storage/checkpoints/governance.sqlite`。抽取、验证和搜索均写入 hash-only
`ModelRun` 审计记录，不把提示词、原文、API Key 或模型自由文本写入 SQLite。

默认 `TRUSTKB_GOVERNANCE_T1_DOMAINS=[]` 和 `T2_DOMAINS=[]` 是有意的 fail-closed 设置；
只有经过人工批准的权威域名才应加入对应 JSON 数组。完整策略见
[L3 设计规格](docs/superpowers/specs/2026-07-28-l3-claim-evidence-quality-governance-design.md)。

## 当前可用：L4 安全发布与混合检索

L4 只发布已经通过 L3 的 `VERIFIED`、`USER_ASSERTED` 或 `OPINION` Claim。模型只能选择
标题和分组，Markdown 中的事实句、Claim ID、来源、质量状态与内容哈希均由代码确定性生成。
发布采用可恢复 Saga：先验证 Vault 暂存文件，再写入并强读回 Milvus，随后原子替换生成笔记，
最后才在同一 SQLite 事务中切换当前版本与索引代际。索引可自动重建；Vault 哈希不一致则阻断，
不会静默覆盖人工修改。

首次运行安装本地检索依赖并启动 WSL Docker 中的 Milvus Standalone：

```powershell
uv sync --extra dev --extra retrieval --extra bge
powershell -ExecutionPolicy Bypass -File scripts/milvus.ps1 start
uv run alembic upgrade head
uv run trustworthy-kb-publication generation create
```

在 `.env` 中配置本地 Vault 和模型目录后，可发布一条已处于 `PUBLISH_INTENT` 的变化并检索：

```powershell
uv run trustworthy-kb-publication publish <knowledge_change_id> --path 40-Concepts/Example.md
uv run trustworthy-kb-publication retrieve "要查询的问题" --top-k 5
uv run trustworthy-kb-publication reconcile
```

P0 默认使用 `BAAI/bge-m3` 的 1024 维 dense embedding、Milvus 内置 BM25、RRF `k=60`
和 `BAAI/bge-reranker-v2-m3`。Milvus 仅绑定 `127.0.0.1:19530`，数据保存在 Docker named
volume；`scripts/milvus.ps1 status|logs|stop` 可管理服务。LLM 仍通过统一模型网关调用 WSL
sub2api，可随配置切换 Provider。完整契约见
[L4 设计规格](docs/superpowers/specs/2026-07-28-l4-safe-publication-hybrid-retrieval-design.md)。

## 当前可用：L5 可信问答 API 与评估

L5 只读取当前 `ACTIVE` 索引代际，并依次执行查询规划、混合检索、SQLite 血缘解析、结构化
Claim 生成和独立语义引用验证。任何阶段失败都会返回稳定的拒答码；未验证模型草稿不会进入
JSON 或 SSE。SQLite 只保存问题、计划、答案和引用清单的哈希，已验证答案正文保存在被 Git
忽略的内容寻址快照目录。

先完成迁移、Milvus 与索引代际配置，再启动只监听本机的 API：

```powershell
uv sync --extra dev --extra retrieval --extra bge
uv run alembic upgrade head
uv run trustworthy-kb-api
```

默认端点为 `http://127.0.0.1:8765`：`GET /health/live`、`GET /health/ready`、
`POST /v1/answers` 和 `POST /v1/answers/stream`。请求使用严格 JSON，例如：

```json
{"question":"这份可信知识库对该主题记录了什么？","scope":"auto","top_k":5}
```

提交的合成 Golden 集会在 CI 中执行，不调用网络或模型：

```powershell
uv run trustworthy-kb-eval deterministic
```

RAGAS 是显式、可选的本地评估，使用同一 `LLMSettings` Provider 边界；它不会读取真实 Vault，
输入必须先转换为经过审查的本地 JSONL：

```powershell
uv sync --extra dev --extra eval
uv run trustworthy-kb-eval ragas evals/golden/ragas-synthetic.jsonl
```

完整拒答、SSE、隐私和评估契约见
[L5 设计规格](docs/superpowers/specs/2026-07-28-l5-trusted-qa-api-evaluation-design.md)。

## 开发与验证

```powershell
uv sync --extra dev --extra retrieval
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -m "not integration" --cov=trustworthy_kb
uv run trustworthy-kb-eval deterministic
uv run python scripts/check_public_repository.py
uv run alembic upgrade head
uv build
```

真实 sub2api 测试默认跳过。只有在当前进程已安全注入 key 后，才显式设置
`TRUSTKB_RUN_SUB2API_INTEGRATION=1` 并运行
`uv run pytest tests/integration/test_sub2api.py`。测试只发送合成内容。

真实 Milvus 测试同样是显式集成测试；服务启动后运行
`uv run pytest tests/integration/test_milvus.py -q`，测试只写入一个随机命名的临时 Collection，
并在完成后精确删除该 Collection。

真实 BGE 测试会首次下载较大的本地权重，因此也必须显式开启。下载器只请求推理所需文件，
跳过 ONNX 等替代资产；也可以把 `TRUSTKB_RETRIEVAL_EMBEDDING_MODEL` 和
`TRUSTKB_RETRIEVAL_RERANKER_MODEL` 指向已校验的本地目录：

```powershell
$env:TRUSTKB_RUN_BGE_INTEGRATION = "1"
uv run --extra bge pytest tests/integration/test_bge.py -q
```

完整 P0 合成闭环会同时调用本机 sub2api、BGE、Milvus 和临时 SQLite，并验证最终引用与
数据库正文隔离；上游账户必须可用，失败时测试和服务都会安全拒答：

```powershell
$env:TRUSTKB_RUN_P0_INTEGRATION = "1"
uv run --extra retrieval --extra bge pytest tests/integration/test_p0_trusted_answer.py -q
```

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
