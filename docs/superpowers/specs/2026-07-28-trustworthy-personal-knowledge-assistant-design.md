# 可信个人知识助手与 Obsidian 知识库设计

> 状态：设计基线，已确认开发顺序与公开仓库策略，等待书面规格复核
> 日期：2026-07-28
> 架构形态：单 Agent、单机优先、本地知识真源、云模型可选
> 当前基线：本文件取代早期多 Agent 方案，作为后续实现计划的唯一依据
> 交付策略：基础设施分层推进，先稳定控制面和边界，再完成端到端可信闭环

## 1. 结论先行

本项目不是“给 Obsidian 加一个聊天框”，而是一套：

> **以 Obsidian 为人工可读知识真源，具备知识版本、Claim 血缘、可信度质检、混合检索、可追溯问答和防污染能力的个人知识编译系统。**

原始资料相当于源代码，AI 整理稿相当于编译产物，Milvus 相当于可重建的检索索引。系统必须保证：

1. 原文保留多个不可覆盖版本。
2. 整理稿只展示一个当前生效版本。
3. 原文更新而新整理稿尚未验证时，保留旧整理稿并标记过期待验证。
4. AI 生成内容不能成为证明自身正确的独立证据。
5. 未验证、证据不足、存在冲突或疑似污染的知识不能进入默认问答域。
6. Milvus、缓存和 AI 输出都不是知识真源。
7. 知识问答默认只依据可引用证据；证据不足时明确拒答或说明不确定。

项目仍然采用单 Agent。知识抽取、证据检索、质量检查、发布和索引是 LangGraph 中的确定性节点或模型调用，不是多个自主 Agent。

## 2. 已确认的产品约束

| 维度 | 已确认选择 |
|---|---|
| 产品定位 | 个人开发助手，偏知识整理、学习和知识问答 |
| Agent 数量 | 单 Agent |
| 人工阅读入口 | Obsidian |
| 未来入口 | 独立学习与知识查阅前端 |
| 知识真源 | Obsidian Vault 中的 Markdown 与原始附件 |
| 原文版本 | 保留多个版本，不静默覆盖 |
| 整理稿版本 | 普通界面只展示最新生效版本，历史内部保留 |
| 验证空窗 | 保留上一个已验证整理稿，标记 STALE_PENDING_REVIEW |
| 外部验证 | 允许访问互联网，但优先使用官方文档、论文、RFC、源码等来源 |
| 自动整理 | 低风险、高置信操作可自动执行；高风险变更需要复核 |
| 删除策略 | 默认软删除或回收站，不做自动永久删除 |
| 部署方式 | Windows 本机，Milvus Standalone 通过 Docker/WSL2 |

### 2.1 公开仓库与隐私边界

- GitHub 仓库公开，只发布项目源代码、通用配置、架构文档和合成测试数据。
- 真实 Obsidian Vault、个人笔记、附件、数据库、索引、模型缓存、运行日志、Trace、评估语料和凭据不得进入 Git 历史。
- 测试、示例、截图和故障日志必须使用合成数据，并移除用户名、本机绝对路径、私人 URL 和可识别内容。
- `.env`、密钥、Token、证书和本地服务凭据只保存在本机安全存储；仓库只提供脱敏的示例配置。
- 首次推送前必须启用忽略规则、Secret 扫描、隐私路径检查和暂存区复核；任一检查失败时禁止推送。
- 在明确选择开源许可证前，公开仓库不添加许可证文件，避免默认授予未审定的使用权。

## 3. 目标与非目标

### 3.1 第一阶段目标

- 保存、整理和关联 Obsidian 笔记。
- 导入网页、PDF、Markdown 和代码资料，最终规范化为可审计知识。
- 使用 Dense + BM25 + RRF + Reranker 完成混合检索。
- 提供带来源、版本和引用位置的知识问答。
- 对新增、修改和 AI 派生知识进行可信度质检。
- 识别过期、冲突、证据不足、提示注入和敏感信息。
- 对原文、Claim、整理稿、向量和答案建立完整血缘。
- 支持失败重试、重新索引、回滚和审计。

### 3.2 第一阶段非目标

- 不实现多 Agent 协作。
- 不构建微服务集群、Kafka、Kubernetes 或复杂事件平台。
- 不让 AI 自动执行高风险 Shell、数据库或 Git 操作。
- 不把所有用户笔记都强行判定为客观事实。
- 不追求“AI 绝对判断真伪”；系统判断的是证据支持度、冲突、时效和风险。
- 不依赖知识图数据库；首期血缘关系存入 SQLite。
- 不开发完整前端；FastAPI + SSE 和 Obsidian 先形成闭环。

## 4. 技术栈

| 层 | 技术 | 作用 |
|---|---|---|
| 主语言 | Python 3.12+ | 核心服务、工作流和数据处理 |
| API | FastAPI、Pydantic v2 | 接口、Schema、校验和 SSE |
| Agent 工作流 | LangGraph | 单 Agent 状态机、节点编排、暂停与恢复 |
| RAG 组件 | LangChain | Loader、Retriever 和模型适配 |
| 知识入口 | Obsidian 1.12+、官方 CLI | 读写、移动、属性、链接、历史和审计 |
| 向量库 | Milvus Standalone | Dense、Sparse、元数据过滤和混合检索 |
| 稠密向量 | BGE-M3 | 中文与多语言语义向量 |
| 关键词检索 | Milvus BM25 | 稀疏召回 |
| 融合 | RRF | 合并 Dense 与 BM25 候选 |
| 重排 | BGE-Reranker-v2-m3 | 对候选进行精排 |
| 控制数据库 | SQLite | 版本、Claim、证据、状态、任务、审计和 LangGraph Checkpoint |
| 流式输出 | SSE | 返回工作进度、质检状态和答案 |
| 评估 | RAGAS、Golden Dataset | 检索、忠实度、引用和拒答评估 |
| 可观测 | LangSmith 或 OpenTelemetry | Trace、模型调用、耗时和失败分析 |
| 测试 | pytest、pytest-asyncio | 单元、集成和端到端测试 |
| 部署 | Docker Compose | Milvus 及依赖服务 |

Chroma 不进入当前基线，避免与 Milvus 重复。SQLite 不保存向量，只保存业务控制状态。

## 5. 核心设计原则

### 5.1 原文与整理稿分离

- 原文保存用户实际输入或外部资料快照。
- AI 不直接覆盖原文。
- AI 整理稿包含结构化摘要、概念、Claim、链接、标签和适用范围。
- 用户可以从整理稿回到具体原文版本和段落。
- 用户修改整理稿时，系统必须区分“人工确认”与“AI 再生成”。

### 5.2 Claim 级血缘，而非笔记级真假

一篇笔记可能同时包含事实、观点、个人经验、预测和错误。系统把可判断内容拆成原子 Claim，分别记录类型、范围、时间、版本、证据和状态。

### 5.3 证据优先于模型自信

模型自己的置信度不能作为事实依据。质量裁决必须结合：

- 来源等级
- 证据覆盖度
- 来源独立性
- 多来源一致性
- 时间与版本适配
- 反证检索结果
- 解析质量
- 规则校验
- 必要时的可执行验证

### 5.4 衍生数据可重建

整理稿、Claim、Chunk、Embedding、BM25 索引、缓存和质量报告都必须能从原文版本重新生成。

### 5.5 默认安全失败

验证失败、索引失败、权限检查失败、来源不可追溯时：

- 保留原始资料；
- 不激活新整理稿；
- 不把未验证内容加入默认问答；
- 不退化为“让模型凭训练记忆回答”；
- 保留旧的已验证版本并显示过期提示。

### 5.6 对话、运行状态与知识分离

系统中四类内容不能混为一谈：

| 内容 | 保存位置 | 是否自动成为知识 |
|---|---|---|
| 聊天记录 | SQLite 会话记录 | 否 |
| LangGraph 运行状态 | SQLite Checkpoint | 否 |
| 用户明确陈述的经历、偏好和决定 | Obsidian / SQLite，标记 USER_ASSERTED | 仅在个人作用域内 |
| 有来源的外部或项目知识 | Obsidian + Claim 血缘 | 通过质量门后可以 |

模型在对话中作出的猜测、总结和临时推理不能自动进入长期知识。用户对自身经历和偏好拥有最高陈述权，但用户确认一条公共技术事实只代表“允许个人使用”，不等同于外部证据已经证明该事实。

## 6. 总体架构

~~~mermaid
flowchart TB
    U["用户"] --> O["Obsidian Vault"]
    U --> API["FastAPI + SSE"]

    API --> G["PersonalDevAgent / LangGraph"]
    O <--> OA["Obsidian Adapter"]
    OA <--> G

    subgraph Workflow["单 Agent 工作流"]
        R["Intent Router"]
        I["Ingestion / Parser"]
        C["Claim Extractor"]
        Q["Knowledge Quality Gate"]
        P["Policy Decision"]
        W["Curated Publisher"]
        A["Answer Generator"]
        V["Citation Verifier"]
        R --> I --> C --> Q --> P --> W
        R --> A --> V
    end

    G <--> Workflow
    Q --> ER["Independent Evidence Retriever"]
    ER --> WEB["官方网页 / 论文 / RFC / 源码"]
    ER --> M["Milvus Hybrid Retrieval"]

    DB[("SQLite Control Plane")]
    MVS[("Milvus Derived Index")]
    BLOB[("Local Blob / Attachments")]

    Workflow <--> DB
    I <--> BLOB
    W --> OA
    W --> MVS
    M <--> MVS
    V --> DB
~~~

### 6.1 数据所有权

| 数据 | 权威位置 | 说明 |
|---|---|---|
| 原始 Markdown | Obsidian Vault | 人工可读知识真源 |
| PDF、图片、网页快照 | Vault Attachments / Local Blob | 原始证据 |
| 原文历史版本 | Vault 版本目录或内容寻址快照 | 不只依赖 Obsidian Sync 的保留期限 |
| 当前整理稿 | Obsidian Vault | 人工查阅的最新生效内容 |
| Claim、Evidence、Decision | SQLite | 机器可查询的血缘和裁决 |
| 工作流状态 | SQLite | LangGraph Checkpoint、Job 和重试 |
| Dense/Sparse 索引 | Milvus | 可删除、可重建 |
| 模型 Trace | LangSmith / 本地审计 | 默认脱敏 |

## 7. 模块边界

### 7.1 Obsidian Adapter

职责：

- 使用官方 Obsidian CLI 读写笔记。
- 管理 Properties、Tags、Backlinks、Outgoing Links 和 Unresolved Links。
- 执行创建、移动、追加、历史查看和恢复。
- 写入前读取 content hash，防止并发覆盖。
- 写入后重新读取文件并校验内容、属性和链接。

禁止：

- 绕开路径策略写入 Vault 外部。
- 直接永久删除。
- 在用户编辑期间静默覆盖文件。

### 7.2 Ingestion Worker

职责：

- 接收 Obsidian 笔记、网页、PDF、Markdown 和代码资料。
- 保存不可变来源版本。
- 解析标题、章节、段落、表格、代码、页码和附件。
- 执行去重、敏感信息扫描和提示注入扫描。
- 计算 SHA-256、规范化哈希和近重复指纹。

### 7.3 Claim Extractor

职责：

- 从内容中拆分原子 Claim。
- 分类为 FACT、VERSIONED_FACT、TEMPORAL、USER_ASSERTED、OPINION、PREFERENCE、PROCEDURE、CODE 或 DECISION。
- 保存主体、谓词、客体、否定、适用范围、版本、时间和原文锚点。
- 不作最终真实性裁决。

### 7.4 Evidence Retriever

职责：

- 对每条 Claim 分别检索支持证据与反证。
- 优先官方文档、规范、论文和一手源码。
- 排除当前 Claim 的衍生摘要和近重复副本。
- 识别多个页面是否来自同一上游来源。
- 生成结构化 Evidence Pack。

### 7.5 Knowledge Quality Gate

由确定性规则和独立验证模型调用组成，不是第二个 Agent。

职责：

- Schema、内容完整性、来源和安全检查。
- 判断证据是否支持 Claim。
- 检测冲突、过期、版本不匹配和证据不足。
- 根据风险等级决定自动通过、隔离或人工复核。
- 保存裁决理由、证据、模型和规则版本。

### 7.6 Curated Publisher

职责：

- 根据已通过 Claim 生成当前整理稿。
- 先写入隐藏 Staging 区。
- 完成写后校验和索引后再激活。
- 维护 current_curated_version 指针。
- 旧整理稿内部保留但不在普通界面显示。

### 7.7 Milvus Indexer 与 Reconciler

职责：

- 为 ACTIVE 知识生成 Dense 和 Sparse 数据。
- 使用稳定 Chunk ID 和版本 ID 幂等 Upsert。
- 删除或停用过期 Chunk。
- 周期性对比 Obsidian、SQLite 和 Milvus。
- 支持全量重建、模型迁移和索引回滚。

### 7.8 Q&A Pipeline

职责：

- BGE-M3 Dense Search。
- Milvus BM25 Sparse Search。
- RRF 融合。
- BGE-Reranker 将候选精排。
- 按质量状态、版本、时间、隐私和来源过滤。
- 生成答案后逐 Claim 检查引用是否真正支持结论。

## 8. Obsidian 信息架构

建议目录：

~~~text
00-Inbox/
10-Projects/
20-Areas/
30-Resources/
40-Concepts/
50-Dev-Logs/
60-ADRs/
90-Archive/
Templates/
_Sources/
  Current/
  Versions/
_AI/
  Staging/
  Review/
  Quarantine/
  Reports/
Attachments/
~~~

说明：

- 00-Inbox 保存未整理输入。
- _Sources/Current 保存当前原文。
- _Sources/Versions 保存不可变历史版本或版本引用。
- 正式目录只存当前生效整理稿。
- _AI/Staging 不进入正式检索。
- _AI/Review 保存证据不足、冲突或需要人工决定的内容。
- _AI/Quarantine 保存疑似污染内容，默认不提供给模型。

建议 Frontmatter：

~~~yaml
id: note_01J...
type: concept
domain: engineering
project: personal-kb
status: active
quality_status: verified
source_ids:
  - source_01J...
source_version_ids:
  - source_version_01J...
curated_version: 4
valid_from: 2026-07-28
valid_to:
freshness_at: 2026-10-28
tags:
  - langgraph
  - rag
related:
  - "[[Milvus Hybrid Retrieval]]"
ai_processed: true
ai_model: model-name
quality_policy_version: 1
content_hash: sha256:...
created: 2026-07-28
updated: 2026-07-28
~~~

## 9. 知识血缘与数据模型

### 9.1 血缘链

~~~text
Source
  → SourceVersion
  → ContentBlock
  → Claim
  → Evidence
  → QualityDecision
  → CuratedVersion
  → Chunk
  → Retrieval
  → AnswerClaim
  → Citation
~~~

### 9.2 SQLite 核心对象

| 对象 | 关键字段 | 作用 |
|---|---|---|
| sources | id、type、uri、owner、trust_tier | 稳定来源身份 |
| source_versions | id、source_id、hash、captured_at、path | 不可变版本 |
| content_blocks | id、version_id、type、anchor、text_hash | 结构化内容 |
| claims | id、type、subject、predicate、object、scope | 原子知识 |
| claim_origins | claim_id、block_id、transform_run_id | Claim 来源 |
| evidence | id、source_version_id、anchor、stance | 支持或反对证据 |
| evidence_families | id、canonical_origin | 防止转载被当成独立来源 |
| quality_checks | claim_id、verdict、dimensions、reason | 质检结果 |
| knowledge_changes | id、base_version、target_version、diff | 知识变更 |
| curated_versions | id、note_id、status、based_on | 整理稿版本 |
| lineage_edges | from_id、to_id、relation | 衍生依赖图 |
| index_jobs | object_id、status、attempt、error | 索引状态 |
| model_runs | purpose、model、prompt_version、tokens | 模型调用 |
| operation_logs | actor、action、target、before、after | 审计 |
| graph_checkpoints | thread_id、state、version | LangGraph 恢复 |

### 9.3 Milvus 元数据

每个 Chunk 至少携带：

~~~json
{
  "chunk_id": "chunk_01J...",
  "note_id": "note_01J...",
  "curated_version_id": "curated_01J...",
  "claim_ids": ["claim_01J..."],
  "quality_status": "VERIFIED",
  "source_type": "official_documentation",
  "source_version": "1.0",
  "valid_from": "2026-07-28",
  "valid_to": null,
  "freshness_at": "2026-10-28",
  "sensitivity": "private",
  "index_generation": 3,
  "embedding_model": "BAAI/bge-m3",
  "chunker_version": 2
}
~~~

## 10. 状态机

### 10.1 原文状态

~~~text
CAPTURED
  → PARSED
  → READY

CAPTURED/PARSED
  → PARSE_FAILED
  → QUARANTINED
  → DELETED
~~~

### 10.2 Claim 状态

~~~text
PROPOSED
  → VERIFIED
  → USER_ASSERTED
  → OPINION
  → INSUFFICIENT
  → CONTESTED
  → OUTDATED
  → REJECTED
  → SUPERSEDED
~~~

### 10.3 整理稿状态

~~~text
DRAFT
  → VALIDATING
  → STAGING
  → ACTIVE

ACTIVE
  → STALE_PENDING_REVIEW
  → SUPERSEDED

VALIDATING/STAGING
  → QUARANTINED
  → FAILED
~~~

### 10.4 索引状态

~~~text
PENDING
  → INDEXING
  → INDEXED
  → ACTIVE_INDEXED

INDEXING
  → FAILED

ACTIVE_INDEXED
  → DELETE_PENDING
  → DELETED
~~~

只有 ACTIVE_INDEXED 默认进入正式问答。

human_override 是人工使用决策，不是新的真实性状态。它不能把 CONTESTED 或 INSUFFICIENT 自动改写成 VERIFIED。

## 11. 知识质量门

### 11.1 质量检查顺序

~~~mermaid
flowchart TD
    N["新增或修改知识"] --> S["保存原文版本与 Diff"]
    S --> D["确定性校验"]
    D --> C["原子 Claim 拆分"]
    C --> T["事实类型识别"]
    T --> E["支持证据 + 反证检索"]
    E --> V["Evidence-only Verifier"]
    V --> F["冲突 / 时效 / 版本检查"]
    F --> R["风险策略"]
    R --> P{"裁决"}
    P -->|通过| A["发布整理稿与索引"]
    P -->|证据不足/冲突| H["_AI/Review"]
    P -->|污染/注入| Q["_AI/Quarantine"]
    P -->|明确错误| X["拒绝激活并保留审计"]
~~~

### 11.2 裁决维度

不使用单一 ai_confidence 代替全部判断。至少保留：

| 维度 | 含义 |
|---|---|
| evidence_coverage | Claim 有多少部分被证据覆盖 |
| source_reliability | 来源等级和一手程度 |
| source_independence | 多来源是否真正独立 |
| source_agreement | 支持与反证的一致程度 |
| freshness | 信息是否仍在有效时间内 |
| version_match | 软件、规范或项目版本是否一致 |
| extraction_quality | 解析、OCR 和 Claim 抽取质量 |
| verifier_agreement | 多次验证结果是否稳定 |
| risk_level | 错误后果大小 |

### 11.3 来源等级

| 等级 | 来源 | 默认政策 |
|---|---|---|
| T0 | 用户对自身经历、偏好和决定的明确陈述 | 作为 USER_ASSERTED，不升级为通用事实 |
| T1 | 官方文档、标准、RFC、论文原文、源码、原始数据 | 技术事实优先 |
| T2 | 官方博客、维护者说明、权威机构报告 | 可作主证据，需检查时间和版本 |
| T3 | 高质量二手技术资料 | 作为辅助证据 |
| T4 | 论坛、聚合内容、无明确作者资料 | 默认只作线索 |
| T5 | AI 生成内容、当前知识的衍生摘要 | 不能作为独立事实证据 |

### 11.4 风险政策

| 变更 | 默认处理 |
|---|---|
| 格式、标签、链接建议 | 高置信可自动应用 |
| 摘要、标题、分类 | 通过语义保持检查后应用 |
| 新增普通事实 | 至少一份匹配版本的高等级证据 |
| 修改已有事实 | 必须检查旧 Claim、反证和影响范围 |
| 软件 API、命令、配置 | 绑定版本；优先官方资料；可执行验证 |
| 安全、法律、医疗、财务 | 不自动激活，强制人工确认 |
| 批量移动、覆盖、合并 | 预览 Diff，人工确认 |
| 永久删除 | 默认禁止 |

## 12. 核心工作流

### 12.1 新增知识

1. 用户或导入器把内容放入 Inbox。
2. 系统保存原文版本、哈希和来源。
3. 执行解析、去重、Secret 与 Prompt Injection 扫描。
4. 抽取 Claim 和结构关系。
5. 检索独立支持证据与反证。
6. 质量门逐 Claim 裁决。
7. 通过的 Claim 生成整理稿草案。
8. 草案写入 _AI/Staging。
9. 写后校验 Markdown、Properties、Wikilink 和内容哈希。
10. 写入 Milvus Staging Generation。
11. 验证索引可搜索且元数据正确。
12. 激活整理稿和索引 Generation。
13. 生成质量报告和审计记录。

### 12.2 修改知识

1. 读取当前 source_version 和 content_hash。
2. 保存新原文版本及语义 Diff。
3. 找出受影响 ContentBlock、Claim、整理稿和 Chunk。
4. 当前整理稿进入 STALE_PENDING_REVIEW。
5. 只重跑受影响血缘子图。
6. 新整理稿通过后切换 current 指针。
7. 旧整理稿进入 SUPERSEDED。
8. 旧 Chunk 失效，新 Chunk 激活。
9. 清理或失效相关答案缓存。

### 12.3 知识问答

~~~text
用户问题
  → 意图、时间、版本和个人/通用范围识别
  → 质量、隐私、版本硬过滤
  → BGE-M3 Dense Search
  + Milvus BM25
  → RRF
  → 去重与来源多样性
  → BGE Reranker Top 30 → Top 5
  → Evidence Pack
  → 单 Agent 生成答案
  → Answer Claim 拆分
  → Claim-Citation 一致性验证
  → 带 Obsidian Wikilink、来源和不确定性返回
~~~

### 12.4 删除知识

- 默认移动到回收站并标记 DELETED。
- 立即禁止新问答召回。
- 创建 Milvus DELETE_PENDING 任务。
- 失效缓存、整理稿和衍生 Claim。
- 保留必要审计，但敏感内容按隐私策略清除。
- Reconciler 确认 Vault、SQLite、Milvus 和缓存状态一致。

### 12.5 模型或索引升级

- 新旧 Embedding 不在同一 Generation 混用。
- 建立新 Collection 或新 Generation。
- 后台全量重建并运行 Golden Queries。
- 指标达标后切换 active_generation。
- 失败时继续使用旧索引。

## 13. 边界情况与处理方式

### 13.1 采集与来源

| 问题 | 风险 | 处理 |
|---|---|---|
| 同一文档重复导入 | 重复知识占满 Top-K | URL、文件哈希、规范化哈希和近重复指纹去重 |
| 多个网页转载同一原文 | 伪造多来源一致性 | 归入同一 evidence_family |
| 网页静默更新 | 旧事实继续生效 | 哈希变化生成新版本并触发影响分析 |
| 网页 404 或不可访问 | 无法复核 | 保留最后快照，标记来源不可访问 |
| 登录、付费墙导致只抓到摘要 | 残缺内容被当全文 | 保存抓取完整度，低完整度不自动验证 |
| MIME 或扩展名伪装 | 解析错误或恶意文件 | 检查魔数、MIME、大小和解析器沙箱 |
| 超大文件或压缩炸弹 | 资源耗尽 | 大小、页数、解压比例和处理时间限制 |
| 相对时间“昨天” | 时间语义漂移 | 解析为绝对时间并保留原始表达 |
| 来源没有作者、日期、版本 | 难以判断可靠性 | 降低来源等级，必要时隔离 |
| 采集时用户正在编辑 | 保存半成品 | 文件稳定窗口、哈希复检和乐观锁 |

### 13.2 解析、OCR 与多模态

| 问题 | 风险 | 处理 |
|---|---|---|
| OCR 把 0/O、1/l 混淆 | 数字和代码错误 | 保存 OCR 置信度；关键数字回看原图 |
| 表格丢失表头 | 单元格失去语义 | Chunk 携带表名、列名和行上下文 |
| PDF 双栏顺序错乱 | 语义拼接错误 | 布局解析和阅读顺序校验 |
| 公式变成乱码 | 推导错误 | 保存公式原图或 LaTeX；低质量不抽取事实 |
| 脚注与正文错配 | 引用对象错误 | 页码和脚注锚点绑定 |
| 图片中文字未解析 | 遗漏重要内容 | 标记 extraction_incomplete，不宣称完整 |
| 代码与输出错位 | 错误结论 | 代码块和邻接输出作为结构化对保存 |
| Markdown Frontmatter 损坏 | Obsidian 无法解析 | Schema 校验和写后回读 |
| 编码和 Unicode 异常 | 隐藏指令或检索差异 | 原文保留，规范化副本用于扫描和索引 |

### 13.3 Claim 抽取与语义保持

| 问题 | 风险 | 处理 |
|---|---|---|
| 复合句只验证一部分 | 半真半假整体通过 | 拆分最小可验证 Claim |
| 否定词丢失 | 事实反转 | 保存 polarity，并做原文蕴含检查 |
| “可能”变成“必然” | 夸大结论 | 保存 modality：may、should、must |
| 条件和例外被摘要删除 | 错误泛化 | Claim 保存条件、范围和例外 |
| 代词指向错误实体 | 错误关联 | 实体消歧；不确定时保留上下文 |
| 同名项目、人物或概念 | 知识串线 | 稳定 entity_id + domain/project scope |
| 单位、币种、时区错误 | 数值误导 | 数值结构化并保留原始单位 |
| 计划被写成已完成 | 状态误判 | 区分 planned、in_progress、completed |
| 观点被当成事实 | 错误验证 | 分类为 OPINION/PREFERENCE |
| 用户经历被当成公共规律 | 错误外推 | 标记 USER_ASSERTED 和 owner scope |
| 翻译改变技术含义 | 错误术语 | 保存原文术语和翻译版本 |
| 多次摘要导致语义漂移 | 逐代失真 | 每次整理基于原文，不基于旧摘要的摘要 |

### 13.4 证据与可信度

| 问题 | 风险 | 处理 |
|---|---|---|
| AI 用自己产物作证据 | 循环验证 | T5 衍生内容不得作为独立证据 |
| 多个证据来自同一上游 | 伪独立 | evidence_family 去重 |
| 只搜索支持材料 | 确认偏误 | 并行执行支持检索与反证检索 |
| 没找到证据就判假 | 错误拒绝新知识 | 标记 INSUFFICIENT，不等同 REJECTED |
| 官方文档版本不符 | 旧能力套到新版本 | 强制 version_match |
| 两个权威来源冲突 | 用户误判 | 标记 CONTESTED，保留双方及适用范围 |
| 官方文档本身过期 | 权威但不新鲜 | 检查发布日期、更新日和产品版本 |
| 证据只支持部分 Claim | 过度推断 | 计算 evidence_coverage，拆分剩余部分 |
| 新知识只有单一官方来源 | 无法交叉验证 | 允许单一权威来源，但显示限制 |
| 网页验证时原文被修改 | 证据不可复现 | 保存 evidence snapshot/hash |
| 引用链接仍在但内容变了 | 历史答案失真 | 引用绑定 source_version 和 anchor |
| 相互冲突实际是范围不同 | 误报冲突 | 先比较实体、时间、地区、版本和条件 |
| 用户坚持采用被证据反驳的公共事实 | 人工审批被误当成真值证明 | 保持 CONTESTED，附加 human_override；只在个人作用域带警告使用 |
| 用户纠正自己的经历或偏好 | 旧个人知识继续生效 | 用户是个人事实权威，新版本 supersede 旧 USER_ASSERTED |

### 13.5 AI 验证器

| 问题 | 风险 | 处理 |
|---|---|---|
| 生成和验证使用同一上下文 | 关联性幻觉 | 分离 Prompt、上下文和任务目的 |
| 验证器忽略证据自行回答 | 假验证 | Evidence-only Schema；要求逐证据理由 |
| 模型结构化输出失败 | 状态不确定 | Pydantic 校验、有限重试、失败进入复核 |
| 同一输入多次结果不一致 | 不稳定裁决 | 记录 verifier_agreement；高风险需二次验证 |
| Prompt 或模型升级 | 历史标准变化 | 保存模型和 policy 版本；按影响重验 |
| 长证据导致关键部分被截断 | 漏判 | Evidence Pack 预算和分 Claim 验证 |
| 验证器被证据中的指令劫持 | Prompt Injection | 数据分隔、扫描、系统策略重申 |
| 模型过度相信“官方”标签 | 元数据欺骗 | 来源标签由系统生成，不采信文档自述 |
| 高置信但理由与证据不符 | 虚假置信 | 程序化检查引用和结论对齐 |

### 13.6 编辑、并发与版本

| 问题 | 风险 | 处理 |
|---|---|---|
| 用户编辑时 AI 同时写入 | 覆盖用户内容 | content_hash 乐观锁 |
| 两个入口同时修改 | 丢失更新 | base_version + 冲突副本 + 人工合并 |
| AI 结果基于旧原文 | 写入过期结论 | 提交时验证 source_version |
| 一次修改只影响部分知识 | 全量重跑成本高 | 血缘图局部失效和局部重验 |
| 文件改名或移动 | Wikilink 断裂 | 通过 Obsidian CLI 移动并验证链接 |
| 合并两篇笔记 | Claim 重复或丢失 | 映射旧 Claim 到新整理稿并去重 |
| 拆分一篇笔记 | 血缘断开 | 迁移 source_anchor 和 lineage_edges |
| 用户人工纠正 AI | AI 下次又改回去 | 标记 human_confirmed / protected section |
| 插件仅改格式 | 产生大量无意义版本 | 语义哈希与格式哈希分离 |
| 同步生成冲突文件 | 两份当前版本 | 识别 conflict copy，暂停自动发布 |
| 写文件中途崩溃 | 半文件 | 临时文件 + fsync + 原子重命名 |
| 回滚原文 | 下游仍指向新版本 | 回滚视为新版本并重新计算血缘 |

### 13.7 组织、链接与分类

| 问题 | 风险 | 处理 |
|---|---|---|
| 分类置信度不足 | 笔记被移动到错误目录 | 进入 _AI/Review，不自动移动 |
| 一个笔记属于多个主题 | 强制单目录损失语义 | 主目录 + tags/related 多重关联 |
| 标题冲突 | Wikilink 指向错误 | 稳定 note_id、aliases 和路径检查 |
| 自动补链过多 | 知识图噪声 | 链接阈值、每段上限和人工反馈 |
| 相关不等于因果 | 生成错误关系 | relation_type 明确区分 related/causes |
| 孤立笔记 | 无法召回上下文 | orphan 报告，但不强行建立链接 |
| 删除目标笔记 | 出现悬空链接 | unresolved links 报告和替代建议 |
| 批量重命名 | 大范围破坏 | 计划、预览、快照、执行后全库验证 |

### 13.8 Milvus 与索引

| 问题 | 风险 | 处理 |
|---|---|---|
| Obsidian 成功、Milvus 失败 | 人工内容与问答不一致 | index_job + Reconciler；未完成不切 active |
| Milvus 成功、Obsidian 失败 | 搜到不存在的内容 | 两阶段发布和 Generation 过滤 |
| 更新后旧 Chunk 残留 | 返回过期知识 | version_id 过滤 + DELETE_PENDING |
| 重试产生重复 Chunk | 排名被重复内容占据 | 稳定主键和幂等 Upsert |
| Chunk ID 依赖标题 | 改名造成重复 | 基于 note_id、version、block identity |
| Embedding 模型混用 | 相似度不可比较 | embedding_generation 隔离 |
| 分词器变化 | BM25 排名漂移 | analyzer_version + 全量重建 |
| Chunk 策略变化 | 新旧粒度混杂 | chunker_version + Generation 切换 |
| 近重复内容占满 Top-K | 来源多样性不足 | 近重复聚类和每来源上限 |
| 长文档占满 Top-K | 证据单一 | MMR、每文档配额和来源多样性 |
| 标题/章节丢失 | Chunk 语义不足 | Embedding 文本附带结构路径 |
| 索引刚写入读不到 | 用户认为失败 | 写后检查使用 Session/Strong consistency |
| 索引损坏 | 全部问答异常 | 校验统计、快照和从真源重建 |

### 13.9 检索与问答

| 问题 | 风险 | 处理 |
|---|---|---|
| 没有证据仍回答 | 幻觉 | 证据阈值不足时拒答 |
| 引用存在但不支持结论 | 假引用 | Answer Claim-Citation 验证 |
| 混用不同软件版本 | 技术答案错误 | 查询规划提取目标版本并硬过滤 |
| 用户问“现在” | 使用过期知识 | 检查 freshness_at，必要时联网验证 |
| 个人经验被回答成通用结论 | 作用域错误 | personal/general 分开检索 |
| 争议内容只展示一方 | 误导 | 明示 CONTESTED 和主要分歧 |
| 检索到了隔离内容 | 污染答案 | 查询前硬过滤，不做检索后补救 |
| 重排器把高可信证据排低 | 相关性压过可信度 | 最终分数加入质量与来源约束 |
| 一个来源多个 Chunk 被当成多证据 | 虚假一致性 | 文档级和 evidence_family 聚合 |
| 答案省略关键限制条件 | 过度简化 | 生成后范围和条件一致性检查 |
| 缓存答案已经过期 | 旧知识持续传播 | source_version 变化时级联失效缓存 |
| 用户问题本身包含注入 | 绕过系统政策 | 输入规范化、指令边界和权限策略 |
| 模型泄露系统 Prompt | 安全信息暴露 | 不把系统策略作为可检索知识 |
| 引用锚点因编辑变化 | 无法定位 | 引用绑定不可变原文版本 |

### 13.10 代码与开发知识

| 问题 | 风险 | 处理 |
|---|---|---|
| 代码语法正确但逻辑错误 | 用户误用 | 测试、类型检查和示例输入验证 |
| 示例依赖未注明版本 | 无法运行 | 保存 runtime/dependency version |
| 命令只适用于 Linux | Windows 执行失败 | 保存 os/shell scope |
| 命令具有破坏性 | 数据损失 | 风险分类、默认不执行、人工确认 |
| 复制命令包含未替换变量 | 用户直接执行失败 | 检测 placeholder 并显式提示 |
| API 已弃用 | 过期知识 | freshness TTL + 官方文档复验 |
| Stack Overflow 答案过旧 | 二手资料误导 | 降低来源等级，寻找官方依据 |
| 测试依赖网络或生产资源 | 副作用 | 在受限临时环境运行 |
| 运行成功就认为通用正确 | 过度外推 | 运行结果仅证明当前环境和样例 |
| 日志中包含 Secret | 泄露 | 执行前后 Secret redaction |

### 13.11 安全与隐私

| 问题 | 风险 | 处理 |
|---|---|---|
| 文档包含隐藏指令 | RAG 投毒 | 注入扫描、隔离、内容只作数据 |
| 多个安全片段组合成攻击 | 组合注入 | 组装 Context 后再次扫描 |
| 安全文档正常引用攻击样例 | 扫描器误隔离正常资料 | 风险扫描只提供信号，结合来源、结构和使用上下文裁决 |
| 指令经过 Base64、零宽字符或多语言混淆 | 规则扫描漏检 | Unicode 规范化、可疑编码检测和模型辅助分类 |
| Embedding 被恶意操纵 | 恶意内容高频召回 | 异常分布、来源限制、Reranker 检查 |
| 笔记含密码、Token、私钥 | 外泄 | Secret 检测，禁止外发和入普通索引 |
| 外部验证搜索泄露私人事实 | 隐私泄漏 | 查询脱敏和抽象化 |
| 日志保存完整私密正文 | 二次泄漏 | 默认只记录 ID、哈希和脱敏摘要 |
| 删除后向量和缓存仍存在 | 忘记权失效 | 删除对账和清除报告 |
| 未来多用户越权检索 | 数据泄漏 | Chunk 继承 owner、ACL、sensitivity |
| 缓存跨用户共享 | 私密回答泄漏 | user/scope/version 参与缓存键 |
| 本地 Vault 未加密 | 设备丢失泄漏 | 建议全盘加密；敏感附件可二次加密 |
| Connector 权限过大 | 供应链污染 | 最小权限、来源白名单和 Staging |
| 向量库被直接写入 | 绕过质量门 | 只有 Indexer 具有写权限 |

### 13.12 工具和动作

| 问题 | 风险 | 处理 |
|---|---|---|
| 笔记要求执行命令 | 间接工具注入 | 笔记永远不能授权工具 |
| 回答代码被自动执行 | 非预期副作用 | 回答与执行使用独立流程 |
| 路径穿越 Vault 外 | 文件泄漏或破坏 | 路径解析后验证 workspace root |
| Shell 参数被拼接注入 | 任意命令执行 | 参数 Schema 和无 Shell 拼接 |
| 工具返回成功但实际失败 | 虚假完成 | 回读文件、Git 状态或测试结果 |
| 重试导致重复副作用 | 重复写入 | idempotency key 和执行状态 |
| 工具状态未知 | 盲目重试 | 标记 UNKNOWN，先查询实际状态 |
| 高风险动作无批准 | 数据损失 | Policy Gate 和精确 Diff 审批 |

### 13.13 运行、恢复与升级

| 问题 | 风险 | 处理 |
|---|---|---|
| 进程在状态切换中崩溃 | 状态卡住 | 持久 Checkpoint、lease 和幂等恢复 |
| API 限流或模型超时 | 任务失败 | 指数退避、预算和人工复核降级 |
| 离线无法联网验证 | 无法判断新事实 | 保存原文，不激活需要外证的 Claim |
| SQLite 恢复旧备份 | 与 Vault 分叉 | 启动对账，以原文版本重建控制状态 |
| Milvus 不可用 | 问答失败 | 可降级 SQLite/本地关键词搜索并明确提示 |
| 磁盘满 | 文件写入不完整 | 写前空间检查和写后哈希 |
| 系统时间错误 | 版本顺序错误 | 使用 UTC、单调序号和来源时间分离 |
| Prompt/模型升级 | 裁决漂移 | 版本化、Shadow Eval 和按影响重验 |
| 审计日志被修改 | 无法追责 | Append-only + 周期性链式哈希 |
| 备份包含密钥 | 备份泄漏 | 凭据只保存在 OS Keychain |
| 恢复后索引版本不匹配 | 返回错误向量 | active_generation 校验后才开放问答 |
| 后台任务无限重试 | 资源耗尽 | 最大重试、DLQ/Review 和告警 |

### 13.14 未来前端与用户认知

| 问题 | 风险 | 处理 |
|---|---|---|
| 用户把高置信度当绝对正确 | 过度信任 | 展示证据维度，不只显示一个百分比 |
| 过期提示不明显 | 继续使用旧知识 | 明确 STALE 标识、来源版本和更新时间 |
| 争议知识显示成单一结论 | 误导 | 并列展示冲突观点 |
| 人工修改与 AI 修改难区分 | 无法追责 | 显示 actor、Diff 和修改原因 |
| 前端缓存旧内容 | 与 Obsidian 不一致 | ETag/source_version 和事件失效 |
| 历史版本太多影响阅读 | 信息噪声 | 普通界面只显示当前版，历史按需展开 |
| 用户误点批量采纳 | 大范围污染 | 二次确认、采纳数量和影响预览 |
| AI 建议过于频繁 | 用户关闭系统 | 置信阈值、批量摘要和可配置静默 |

## 14. 两阶段发布与一致性

Obsidian、SQLite 和 Milvus 无法形成单个数据库事务，因此使用 Saga + Reconciler：

~~~text
1. 保存 SourceVersion
2. SQLite 写 knowledge_change = VALIDATING
3. 生成 CuratedVersion = STAGING
4. 写 _AI/Staging 并回读校验
5. 写 Milvus staging_generation
6. 使用强一致性验证 Chunk 可检索
7. SQLite 写入 PUBLISH_INTENT，但保持旧 active 指针
8. Obsidian 通过原子重命名发布已验证的 Staging 整理稿
9. 回读正式文件并核对 curated_version_id 与 content_hash
10. SQLite 事务切换 current_curated_version 与 active_generation
11. 旧版本标记 SUPERSEDED
12. Reconciler 周期性检查三方状态
~~~

任何一步失败都可凭 operation_id 幂等继续。第 10 步完成前，问答始终使用旧 ACTIVE 版本。若进程恰好在第 8 步后崩溃，Obsidian 可能短暂显示已经验证但尚未切换检索指针的新整理稿；Reconciler 根据 PUBLISH_INTENT 完成激活或恢复旧文件。未经验证的草案在任何情况下都不能进入正式路径。

## 15. 问答可信度策略

### 15.1 默认可检索状态

| 状态 | 通用知识问答 | 个人历史问答 |
|---|---|---|
| VERIFIED | 允许 | 允许 |
| USER_ASSERTED | 默认排除或注明个人来源 | 允许 |
| OPINION | 仅在询问观点时允许 | 允许并标明观点 |
| STALE_PENDING_REVIEW | 低风险带警告；高风险排除 | 带版本提示 |
| CONTESTED | 仅在展示争议时允许 | 同左 |
| INSUFFICIENT | 默认排除 | 可作为未确认笔记展示 |
| QUARANTINED | 禁止 | 禁止 |
| REJECTED/SUPERSEDED | 禁止 | 历史审计可见 |

### 15.2 正确拒答

以下情况必须拒答或说明无法确认：

- 没有满足质量阈值的证据。
- 唯一证据与问题版本不一致。
- 证据全部过期且问题要求当前事实。
- 引用无法定位到不可变来源版本。
- 权限或隐私过滤失败。
- 检索系统失败。
- Claim-Citation 验证未通过。

## 16. 评估与测试

### 16.1 测试层次

| 层 | 范围 | 重点 |
|---|---|---|
| Unit | 状态转换、哈希、去重、评分、过滤、路径 | 所有公共函数、边界和错误分支 |
| Contract | Pydantic、SQLite、Milvus Metadata、SSE | Schema 兼容性 |
| Integration | Obsidian CLI、SQLite、Milvus、模型 Mock | 写入、失败、恢复和对账 |
| E2E | 新增、修改、问答、删除、重建 | 整个可信闭环 |
| Eval | RAGAS、Golden Claims、引用检查 | 检索和回答质量 |
| Adversarial | 注入、投毒、Secret、路径逃逸 | 防污染和安全 |
| Chaos | 崩溃、超时、重复任务、磁盘满 | 幂等和恢复 |

### 16.2 必测场景

- 原文 v1 通过，v2 修改一条事实，只重验相关 Claim。
- v2 验证期间继续显示 v1 整理稿，并标记过期。
- v2 与官方文档冲突，不激活且进入 Review。
- 三个转载页面只计为一个 evidence_family。
- 恶意笔记包含“忽略系统指令”，不能改变模型或调用工具。
- Obsidian 写成功、Milvus 失败，旧索引继续服务。
- Milvus 写成功、发布失败，staging_generation 不被检索。
- 删除来源后，旧 Chunk、缓存和派生内容全部失效。
- 用户人工纠正后，AI 后续整理不能覆盖保护内容。
- 引用相关但不支持具体结论时，答案验证失败。
- 没有证据时正确拒答，不使用模型训练记忆补全。
- Embedding 模型升级失败时可回滚旧 Generation。

### 16.3 首期质量门槛

| 指标 | 目标 |
|---|---|
| 无来源 AI Claim 自动进入正式库 | 0 |
| QUARANTINED 内容进入默认问答 | 0 |
| 原文修改后受影响索引失效率 | 100% |
| 删除后的衍生数据清理确认率 | 100% |
| 引用可定位到原文版本 | 100% |
| 高风险修改未经人工审批执行 | 0 |
| 核心状态机单元测试覆盖率 | 不低于 80% |
| Golden Dataset Citation Precision | 不低于 95% |
| 崩溃后任务可恢复率 | 不低于 99% |

## 17. 分阶段交付

### 17.1 开发顺序：基础设施分层推进

开发不采用 RAG 演示优先，也不在第一步同时实现全部业务工作流。各层必须通过本层测试和契约检查后，下一层才能依赖它：

1. **L0 工程与安全基线**：仓库结构、依赖锁定、配置模型、日志脱敏、Secret 扫描、隐私忽略规则、静态检查、测试和 CI。
2. **L1 领域与控制面**：ID、枚举、状态转换、SQLite Schema、迁移、Repository、事务边界、操作日志和幂等键。
3. **L2 采集与运行基础设施**：Obsidian Adapter、不可变 SourceVersion、哈希与 Diff、任务队列抽象、LangGraph SQLite Checkpoint 和恢复契约。
4. **L3 Claim 与质量治理**：结构化 Claim、Evidence Pack、来源等级、确定性校验、Verifier 接口、质量裁决和人工复核策略。
5. **L4 发布与检索基础设施**：Staging/Active 发布、Milvus Generation、Dense + BM25 + RRF + Reranker、Reconciler、失效和重建。
6. **L5 API、问答与评估**：FastAPI + SSE、查询规划、引用验证、正确拒答、Golden Dataset、RAGAS、对抗与故障恢复测试。

每层都以可运行迁移、公共接口测试、错误分支测试和隐私检查作为完成条件。虽然实施顺序按基础设施分层，L5 完成前仍必须以第 18 节的 P0 可信闭环作为整体发布门槛。

### 17.2 产品能力阶段

### P0：可信知识闭环

- Obsidian Adapter。
- 原文版本、哈希、Diff 和快照。
- SQLite 控制模型。
- Claim 抽取和类型识别。
- 确定性质量检查。
- 官方证据检索和 Evidence Verifier。
- VERIFIED、CONTESTED、INSUFFICIENT、QUARANTINED。
- Dense + BM25 + RRF + Reranker。
- 两阶段发布、索引对账和引用问答。
- pytest、Golden Dataset 和基础 RAGAS。

### P1：工程增强

- PDF/OCR 和表格结构。
- 支持/反证双通道检索。
- 可执行代码验证沙箱。
- 血缘局部失效和增量重建。
- Prompt/模型/Embedding 版本迁移。
- LangSmith/OpenTelemetry 完整 Trace。

### P2：学习前端

- 当前整理稿、原文和证据对照。
- 知识状态、冲突和时效可视化。
- 人工 Review、批量采纳和回滚。
- 学习卡片、概念图、复习计划和知识盲区。
- 未来多用户时加入完整 ACL 与数据隔离。

## 18. P0 验收定义

首期完成必须同时满足：

1. 用户在 Obsidian 新增或修改知识后，系统能保留原文版本并生成可审计整理稿。
2. 每条事实能追溯到原文位置、证据和质量裁决。
3. 错误、过期、冲突、证据不足和恶意内容不会进入默认问答。
4. 原文更新期间保留旧整理稿并清晰标记过期待验证。
5. 问答使用混合检索、精排和可点击引用。
6. 无足够证据时系统明确拒答。
7. Obsidian、SQLite 或 Milvus 任一阶段失败，都不会激活半成品知识。
8. 删除、回滚和模型升级均有可验证恢复路径。
9. 所有自动变更都有 Diff、actor、证据和 operation_id。
10. 关键边界场景有自动化测试，核心模块覆盖率不低于 80%。

## 19. 简历价值

该项目最有区分度的不是“使用了 LangChain”，而是以下工程闭环：

1. **知识血缘**：原文版本、原子 Claim、证据、整理稿、向量和答案端到端追踪。
2. **可信度治理**：独立证据、反证检索、冲突检测、时效与版本校验。
3. **防污染 RAG**：隔离区、状态过滤、Prompt Injection/Secret 扫描和正确拒答。
4. **一致性工程**：Obsidian、SQLite、Milvus 两阶段发布、幂等重试和 Reconciler。
5. **检索质量**：BGE-M3 + BM25 + RRF + BGE-Reranker 的混合检索。
6. **可评估性**：Golden Dataset、RAGAS、Citation Precision、Adversarial 和 Chaos Tests。

可用于简历的初步表述：

> 设计并实现以 Obsidian 为知识真源的单 Agent 个人知识助手，将新增及变更内容拆分为原子 Claim，结合来源分级、独立证据与反证检索、版本时效校验和 LLM 事实裁决，构建 VERIFIED/CONTESTED/QUARANTINED 知识生命周期；通过 SQLite 血缘控制面与 Milvus 两阶段索引发布，防止幻觉、过期内容、重复证据及 RAG 投毒进入默认问答链路。

## 20. 参考依据

- [OWASP RAG Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)
- [OWASP Prompt Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- [NIST AI RMF Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence)
- [Corrective Retrieval Augmented Generation](https://arxiv.org/abs/2401.15884)
- [Self-RAG](https://arxiv.org/abs/2310.11511)
- [Milvus Consistency](https://milvus.io/docs/consistency.md)
- [Milvus BM25 Function](https://milvus.io/docs/bm25-function.md)
- [Milvus Hybrid Search Retriever](https://milvus.io/docs/milvus_hybrid_search_retriever.md)
- [BGE Reranker v2 m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- [RAGAS Evaluation](https://docs.ragas.io/en/latest/references/evaluate/)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [Obsidian Version History](https://help.obsidian.md/Obsidian%2BSync/Version%2Bhistory)

## 21. 最终设计判断

系统的核心不是让 AI 替用户决定“什么是真理”，而是让每条知识都有：

~~~text
来源
  + 不可变版本
  + 原文锚点
  + Claim 类型
  + 独立证据
  + 反证
  + 时效与适用范围
  + 质量状态
  + 衍生血缘
  + 可回滚记录
~~~

Obsidian保证用户随时能阅读、纠正和带走自己的知识；SQLite保证系统理解知识如何变化；Milvus保证高质量检索；LangGraph保证整个过程可恢复、可审批、可观察。

这四者共同构成项目的可信闭环。
