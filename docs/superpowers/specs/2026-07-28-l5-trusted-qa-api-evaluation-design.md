# L5 可信问答 API 与评估设计

> 日期：2026-07-28
> 状态：已冻结，可直接实施
> 范围：P0 的可信问答编排、FastAPI/SSE、本地审计、Golden Dataset 和基础评估

## 1. 目标与边界

L5 把现有 L0-L4 能力连接成可调用的可信问答闭环：只从当前 ACTIVE Milvus
Generation 检索，通过 SQLite 再确认当前版本，生成结构化 Answer Claims，逐条验证引用，
最后返回带 Obsidian 定位信息的答案；任一可信条件不满足时正确拒答。

本阶段完成：

- 本机单用户 FastAPI API、普通 JSON 响应和 SSE 进度流。
- 问题规范化、个人/通用范围规划、时间与版本约束。
- L4 Dense + BM25 + RRF + Reranker 检索复用。
- 结构化 Answer Claim 生成、引用闭集校验和独立支持关系验证。
- 引用定位、拒答策略、hash-only 运行审计和幂等操作 ID。
- 合成 Golden Dataset、确定性指标、可选 RAGAS 数据导出和评估入口。
- API、对抗、故障和合成端到端测试。

本阶段不完成 PDF/OCR、学习前端、多用户 ACL、外网部署和 P1/P2 功能。删除、回滚、
模型代际升级将在独立生命周期规格中实现，避免让一个规格同时修改问答与发布状态机。

## 2. 方案比较

### 方案 A：直接流式生成答案

模型 token 到达后立即通过 SSE 返回，再在尾部补做引用检查。延迟最低，但已经输出的错误、
幻觉或假引用无法收回，与“验证后发布”的系统原则冲突，不采用。

### 方案 B：结构化生成、验证后输出（采用）

SSE 只发送安全的阶段进度。模型返回结构化 Answer Claims，每个 Claim 只能引用请求提供的
Chunk ID；程序先验证闭集、版本和定位，再由独立模型验证器判断引用是否支持结论。全部通过后，
代码确定性渲染最终答案。优点是安全边界清晰、可测试、可审计；代价是首个答案正文延迟较高。

### 方案 C：纯抽取式回答

只拼接检索片段，不使用生成模型。安全性高，但无法自然归纳多来源内容，无法满足个人知识助手的
使用体验。它适合作为诊断接口，不作为默认问答方式。

## 3. 模块结构

~~~text
trustworthy_kb/
  answer/
    contracts.py       # 严格请求、计划、Answer Claim、引用和结果契约
    planning.py        # provider-neutral 结构化查询规划
    generation.py      # 只基于冻结 Evidence Context 生成 Answer Claims
    verification.py    # 引用闭集、定位和支持关系验证
    rendering.py       # 确定性 Markdown 与可点击引用渲染
    service.py         # 单次问答编排和安全事件流
    audit.py           # hash-only AnswerRun 持久化
    evaluation.py      # Golden Dataset 和确定性指标
    errors.py          # 对外安全错误与拒答原因
  api/
    app.py             # FastAPI app factory、lifespan 和路由
    runtime.py         # SQLite、Milvus、BGE、LLM 共享资源
    sse.py             # 严格 SSE 编码
    cli.py             # loopback-only Uvicorn 启动入口
~~~

每个模块只有一个职责。业务服务依赖 Protocol，不依赖 FastAPI、PyMilvus 或具体模型提供方。

## 4. 严格契约

### 4.1 请求

`AnswerRequest` 包含：

- `question`：去首尾空白后 1-4000 字符，拒绝 NUL 和控制字符。
- `scope`：`general`、`personal` 或 `auto`，默认 `auto`。
- `as_of`：可选带时区时间；未给出时使用服务端 UTC 当前时间。
- `software_version`：可选 1-100 字符版本约束。
- `top_k`：1-10，默认 5。
- `operation_id`：可选幂等键；缺省由请求 hash 和随机 AnswerRun ID 组合。

API 不接受任意 allowed statuses、Generation ID、Milvus filter 或模型 Prompt，避免客户端扩大
可信域。

### 4.2 查询计划

`QueryPlan` 是模型的严格结构化输出：

- `normalized_query`
- `scope`：仅 `general` 或 `personal`
- `requires_current`
- `target_version`
- `include_opinions`

程序覆盖规则优先于模型：显式请求 scope/version/as_of 时模型不能修改；通用范围只允许
VERIFIED，个人范围允许 VERIFIED 和 USER_ASSERTED，只有明确询问观点时才加入 OPINION。
QUARANTINED、INSUFFICIENT、CONTESTED、OUTDATED、REJECTED、SUPERSEDED 永远不能由请求开启。

### 4.3 冻结证据

每个检索命中解析为 `AnswerEvidence`：

- Chunk ID、正文、Claim IDs、质量状态和敏感级别。
- KnowledgeNote ID、CuratedVersion ID、当前 Generation ID。
- Vault 相对路径和 heading path。
- 不可变 SourceVersion IDs。

证据解析时再次读取 SQLite 当前指针。无法解析路径、当前版本或不可变来源版本的命中被丢弃；
全部丢弃则拒答。

### 4.4 Answer Claims

模型不返回自由 Markdown，而是 `AnswerDraft`：

- `claims[]`：每项包含一句完整陈述和 1-N 个 citation Chunk IDs。
- `limitations[]`：只允许描述证据范围，不得加入新事实。

Claim 数量、单句长度和总输出长度均有上限。引用 ID 必须属于冻结证据闭集，禁止 URL、路径、
脚注编号或模型自行构造的来源标识。

### 4.5 最终结果

`AnswerResult` 只有两种终态：

- `answered`：包含确定性渲染文本、Answer Claims、去重引用和 Generation ID。
- `refused`：包含稳定 reason code 和不泄露内部信息的说明，不包含模型猜测。

## 5. 问答数据流

~~~text
validate request
  -> create/reuse AnswerRun(IN_PROGRESS)
  -> structured query plan
  -> resolve ACTIVE generation
  -> build hard-filtered RetrievalQuery
  -> L4 hybrid retrieve + rerank + SQLite current-version check
  -> resolve immutable AnswerEvidence
  -> evidence sufficiency gate
  -> structured AnswerDraft generation
  -> closed-set citation validation
  -> per-claim citation-support verification
  -> deterministic render
  -> persist COMPLETED hash-only audit
  -> return verified answer
~~~

任何步骤失败都进入稳定拒答或安全错误，不回退到模型训练记忆，不回退到 SQLite FTS 伪装成
等价语义检索，也不返回未验证草稿。

## 6. 引用验证与渲染

验证分三层：

1. **确定性结构验证**：引用非空、去重、属于检索闭集，Claim/引用数量和长度合规。
2. **控制面验证**：Chunk 对应当前 CuratedVersion 和 ACTIVE Generation，Vault 路径可定位，
   至少一个不可变 SourceVersion ID 存在。
3. **语义支持验证**：独立结构化模型调用只接收 Claim 与已引用片段，逐条输出
   `supported`、`supporting_chunk_ids` 和稳定 reason code。程序要求所有 Claim 都 supported，
   且 supporting IDs 是原引用的非空子集。

最终 Markdown 由代码渲染，格式为正文句子加 `[n]`，引用区包含：

- `[[Vault/Relative/Path#Heading]]`
- CuratedVersion ID
- SourceVersion IDs
- 质量状态

API 同时返回结构化 citations，客户端不需要解析 Markdown。正文中的脚注编号由代码生成，模型
无法决定编号或目标。

## 7. 正确拒答

稳定拒答代码包括：

- `NO_ACTIVE_GENERATION`
- `NO_TRUSTED_EVIDENCE`
- `EVIDENCE_NOT_LOCATABLE`
- `VERSION_MISMATCH`
- `STALE_EVIDENCE`
- `RETRIEVAL_UNAVAILABLE`
- `PLANNING_FAILED`
- `GENERATION_FAILED`
- `CITATION_VALIDATION_FAILED`
- `POLICY_BLOCKED`

模型超时、格式错误和供应商错误分别映射为规划、生成或引用验证失败；API 不返回 Provider 原始
响应、Prompt、路径、密钥或堆栈。检索失败必须拒答，不静默降级到无证据生成。

## 8. FastAPI 与 SSE

接口：

- `GET /health/live`：只表示进程存活，不触发模型加载。
- `GET /health/ready`：检查 schema、ACTIVE Generation 和 Milvus 可访问性，不输出连接信息。
- `POST /v1/answers`：等待验证完成后返回 `AnswerResult`。
- `POST /v1/answers/stream`：`text/event-stream`，顺序发送 `accepted`、`planned`、
  `retrieved`、`verified`、`answer|refusal`；事件 payload 只使用公开契约。

SSE 使用 FastAPI `StreamingResponse` 和自有编码器，统一 `event:`、`id:`、单行 JSON `data:`，
每个事件以空行结束；事件名来自封闭枚举，JSON 禁止换行注入。客户端断开只取消当前请求，
共享 Milvus/模型资源由 FastAPI lifespan 管理。

服务默认只监听 `127.0.0.1:8765`，不开 CORS，不信任代理头，不提供外网部署配置。P0 是本机
单用户服务；未来开放网络前必须单独设计认证和 ACL。

## 9. 持久化与隐私

迁移新增 `answer_runs`：

- `id`、`operation_id`、`question_hash`、`plan_hash`
- `scope`、`generation_id`、`status`、`refusal_code`
- `answer_hash`、`citation_manifest_hash`
- `model_name`、`prompt_version`
- `started_at`、`completed_at`、`revision`

不保存原始问题、答案正文、检索正文、Prompt 或模型自由文本。ModelRun 延续现有 hash-only 审计；
operation log 只记录 ID、状态和 hash。相同 operation ID 与相同 question hash 返回既有终态；
同 ID 不同问题安全冲突。

## 10. 评估

### 10.1 确定性 Golden Dataset

仓库只提交合成 JSONL，覆盖：可信回答、无证据拒答、个人/通用隔离、版本不匹配、过期、
QUARANTINED、注入、错误引用、Milvus 故障和重复请求。每项包含期望终态、允许引用 Chunk IDs、
禁止引用 IDs 和期望拒答代码。

基础指标：

- Citation Precision = 被 Golden 允许的实际引用 / 全部实际引用，门槛 0.95。
- Retrieval Recall = 召回的期望 Chunk / 期望 Chunk，门槛 0.90。
- Refusal Accuracy = 应拒答样例中正确拒答比例，门槛 1.00。
- Unsafe Citation Count，门槛 0。

这些指标不依赖外部 LLM，可稳定进入 CI。

### 10.2 可选 RAGAS

`eval` optional dependency 固定兼容的 RAGAS 0.4.x。适配器把 Golden 样例导出为
`EvaluationDataset`/JSONL，显式传入统一模型网关适配器；RAGAS 不读取 `.env` 之外的凭据，
不上传真实 Vault 数据。RAGAS 属于显式本地评估，不进入默认 CI，也不能替代确定性引用门槛。

## 11. 配置

新增 `TRUSTKB_ANSWER_` 前缀配置：

- `PROMPT_VERSION=v1`
- `CITATION_VERIFIER_VERSION=v1`
- `MAX_QUESTION_CHARACTERS=4000`
- `MAX_ANSWER_CLAIMS=12`
- `MAX_CLAIM_CHARACTERS=1000`
- `MIN_EVIDENCE_COUNT=1`
- `DEFAULT_TOP_K=5`
- `API_HOST=127.0.0.1`
- `API_PORT=8765`

API host 在 P0 必须是 loopback IP；配置成 `0.0.0.0`、局域网地址或主机名时启动失败。

## 12. 测试与验收

必须覆盖：

- 所有公开契约、验证器、渲染器、SSE 编码器和指标函数。
- 规划模型尝试扩大质量状态时程序覆盖。
- 模型伪造引用、漏引、重复引用、引用不支持 Claim 时拒答。
- 无 ACTIVE Generation、Milvus 失败、BGE 失败、LLM 超时和 SQLite 失败时不返回答案。
- USER_ASSERTED 不进入通用问答，QUARANTINED 永不进入检索。
- SSE 在最终验证前不包含答案正文；终态只有一个。
- 相同 operation ID 幂等，不同 question hash 冲突。
- API 错误不泄露问题正文、Vault 路径、Prompt、key 或 Provider 原始输出。
- 合成端到端：发布 ACTIVE 内容、检索、问答、引用定位和拒答。
- 非集成测试覆盖率继续不低于 80%，Ruff、Mypy、迁移和隐私扫描通过。

## 13. 实施顺序

1. 契约、拒答策略和纯函数测试。
2. AnswerRun 领域模型、迁移和 Repository。
3. 规划、证据解析、生成、验证和渲染。
4. 运行时装配、FastAPI、SSE 和 CLI。
5. Golden Dataset、确定性评估和可选 RAGAS 适配。
6. 合成 E2E、真实 sub2api/BGE/Milvus smoke、文档和 CI。

## 14. 参考实现约束

- FastAPI 资源使用 lifespan 创建和关闭；模型不能在模块导入时加载。
- SSE 基于 FastAPI `StreamingResponse`，但不直接流式输出未验证模型 token。
- RAGAS 使用显式 Evaluation Dataset；外部评估与确定性安全门分离。
- 所有模型调用继续经过 `ModelGateway`/`ModelRouter`，P0 默认走 WSL sub2api，业务代码不
  硬编码 Provider。
