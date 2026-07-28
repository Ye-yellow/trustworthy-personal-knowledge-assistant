# L4 安全发布、混合检索与索引对账设计

日期：2026-07-28  
状态：已冻结  
范围：L4 发布与检索基础设施

## 1. 目标

L4 把 L3 已治理的 Claim 变成可阅读、可检索且可回滚的当前知识，同时保证 Obsidian、SQLite 或 Milvus 任一阶段失败时都不会激活半成品。

本层交付：

- 从 `PUBLISH_INTENT` 变更生成确定性的整理稿草案。
- 在 Vault 的 `_AI/Staging` 中安全写入并回读校验。
- 将整理稿切成稳定 Chunk，生成 Dense 向量，并由 Milvus 内置 BM25 生成 Sparse 向量。
- 使用 Dense + BM25 + RRF 召回，并通过可替换 Reranker 精排。
- 以 SQLite 当前指针做最终版本与质量过滤。
- 支持索引代际建立、验证、切换、回滚、失效和三方对账。
- 提供无 Milvus、无 GPU 的离线测试替身；外部服务不进入默认 CI。

L4 不生成最终答案、不实现 FastAPI/SSE，也不实现 L5 的引用一致性验证。

## 2. 已评估方案

### 方案 A：LangChain Milvus VectorStore 直接贯穿业务

优点是代码短。缺点是代际、BM25 Schema、强一致性验证、幂等主键和失败恢复都被框架默认值遮蔽，难以证明无半发布。淘汰。

### 方案 B：单 Collection，靠 `active` 标志切换

优点是增量写入简单。缺点是 Embedding 维度变化无法原地兼容，标志更新和 SQLite 指针无法成为一个事务，错误窗口较难收敛。可作为未来大规模优化，不作为 P0 基线。

### 方案 C：端口抽象 + PyMilvus 适配器 + Collection 级代际

采用。业务层只依赖 `EmbeddingGateway`、`VectorIndexGateway`、`RerankerGateway` 和 `VaultPublisher`。每个模型/Schema 代际使用独立 Collection；SQLite 记录唯一 ACTIVE 代际。发布任务在目标代际内以稳定 Chunk ID 幂等 upsert，检索结果再与 SQLite 当前整理稿指针对账。这样既能显式控制 Milvus，又能在测试中使用内存替身，并支持模型维度迁移与一键回滚。

## 3. 固定技术决策

| 项目 | P0 决策 |
|---|---|
| Milvus | WSL Docker Compose，固定 2.6.20 Standalone |
| Python SDK | `pymilvus>=2.6.14,<2.7`，放在 `retrieval` 可选依赖 |
| Dense 模型 | `BAAI/bge-m3`，1024 维，归一化向量，COSINE |
| Sparse | Milvus 内置 BM25 Function；原始文本字段启用 analyzer |
| 融合 | Milvus `RRFRanker`，默认 `k=60` |
| 精排 | `BAAI/bge-reranker-v2-m3` 的可选本地适配器；不可用时明确标记降级并按 RRF 排序 |
| 代际 | 一个 `IndexGeneration` 对应一个确定名称的 Collection |
| 写验证 | Milvus Strong consistency；普通检索使用 Bounded consistency |
| 控制真源 | SQLite；Milvus 是可删除、可重建的派生索引 |
| 内容真源 | Obsidian 当前正式整理稿；Staging 不进入默认检索 |

Milvus 官方说明 Windows 可通过 WSL2/Docker Desktop 运行 Standalone，最低内存 8 GiB，并建议 bind mount 数据放在 Linux 文件系统。开发机满足这一基线，部署数据因此保留在 WSL Docker volume，而不是 C 盘源码目录。

## 4. 模块边界

新增 `trustworthy_kb.publication`：

- `contracts.py`：整理稿、Chunk、索引命中、检索查询、对账报告的严格 Pydantic 契约。
- `curation.py`：从可发布 Claim 生成安全 Markdown；LLM 只能组织表达，Claim ID 集合和事实对象由确定性校验约束。
- `chunking.py`：标题感知的稳定 Chunk 切分和确定性 ID。
- `vault.py`：路径策略、原子 Staging 写入、回读哈希校验、正式发布和冲突保护。
- `ports.py`：Embedding、向量索引、Reranker、Vault 的协议。
- `indexing.py`：Collection Schema、代际建立、幂等 upsert、强一致性验证、失效。
- `retrieval.py`：查询嵌入、Dense/BM25/RRF、质量硬过滤、当前版本对账和精排。
- `reconcile.py`：SQLite/Vault/Milvus 三方差异检测与安全修复动作。
- `runner.py`：两阶段发布 Saga 和可重入恢复。
- `cli.py`：`publish`、`retrieve`、`reconcile`、`rebuild` 命令。

新增 `publication.adapters`：

- `milvus.py`：延迟导入 PyMilvus 的生产适配器。
- `bge.py`：延迟导入 FlagEmbedding 的 Dense/Reranker 适配器。
- `in_memory.py`：确定性测试替身，不作为生产降级索引。

配置只出现端口参数，不把 `sub2api`、Milvus 或 BGE 写进领域对象。LLM 继续使用已有统一 `ModelGateway`，用途为 `CURATION`；更换提供方不影响发布协议。

## 5. 契约

### 5.1 整理稿

`CurationDraft` 至少包含：

- `note_id`、`curated_version_id`、`based_on_change_id`。
- `title`、`body_markdown`。
- 按出现顺序去重的 `claim_ids`。
- `quality_statuses`、`source_ids`、`source_version_ids`。
- `sensitivity` 取所有 Claim 中最严格值。
- `model_name`、`prompt_version`、`quality_policy_version`。
- `content_hash`：规范化 Frontmatter 与正文的 SHA-256。

允许自动发布的 Claim 状态为 `VERIFIED`、`USER_ASSERTED`、`OPINION`。后两者必须保留标签和隐私范围，不能伪装为通用事实。`INSUFFICIENT`、`CONTESTED`、`OUTDATED`、`REJECTED`、`SUPERSEDED`、`QUARANTINED` 一律不进入当前整理稿。

LLM 输出必须通过结构化 Schema。随后执行确定性检查：

1. 输出 Claim ID 与输入完全一致，不得增删。
2. 每个事实句至少关联一个输入 Claim。
3. Frontmatter 由程序生成，模型不得生成或覆盖。
4. 禁止脚本、HTML iframe、远程图片和可执行 URI。
5. 内容哈希在写入前后相同。

没有可发布 Claim 时拒绝创建整理稿，不生成空 ACTIVE 笔记。

### 5.2 Chunk

`KnowledgeChunk` 包含：

- `chunk_id`：`sha256(curated_version_id + chunker_version + ordinal + normalized_text)` 的稳定十六进制 ID。
- `note_id`、`curated_version_id`、`claim_ids`。
- `text`、`heading_path`、`ordinal`。
- `quality_status`、`sensitivity`、时效字段。
- `generation_id`、`generation_number`、`embedding_model`、`chunker_version`。

Chunk 不跨二级标题；目标 900 字符，重叠 120 字符，硬上限 1600 字符。单个 Claim 的原子语句不得在中间截断。相同输入和版本必须得到相同 Chunk 集合与 ID。

### 5.3 检索

`RetrievalQuery` 显式声明：

- query 文本、top-k、owner/scope。
- 是否允许 `USER_ASSERTED`、`OPINION`、`STALE_PENDING_REVIEW`、`CONTESTED`。
- sensitivity 上限和时间点。

默认只允许当前 ACTIVE 整理稿中的 `VERIFIED` Chunk。Milvus 表达式先做代际、质量、敏感级别和有效期硬过滤；返回后再从 SQLite 批量加载 `note_id -> current_curated_version_id`，不匹配的命中丢弃。过滤失败或控制面不可用时拒绝检索，不使用未校验结果。

## 6. Milvus Schema

Collection 名称为 `trustworthy_kb_chunks_g{generation_number}`，只允许该固定前缀和十进制编号。

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `chunk_id` | VARCHAR(64), primary | 稳定幂等键 |
| `text` | VARCHAR(8192), analyzer | BM25 输入与引用正文 |
| `sparse` | SPARSE_FLOAT_VECTOR | BM25 Function 输出 |
| `dense` | FLOAT_VECTOR(dim=1024) | BGE-M3 |
| `note_id` | VARCHAR(64) | SQLite Note ID |
| `curated_version_id` | VARCHAR(64) | 当前版本二次校验 |
| `claim_ids_json` | VARCHAR(8192) | 有序 JSON 数组 |
| `quality_status` | VARCHAR(32) | 硬过滤 |
| `sensitivity` | VARCHAR(16) | 隐私过滤 |
| `valid_from_ms` | INT64 | `0` 表示无下界 |
| `valid_to_ms` | INT64 | `0` 表示无上界 |
| `freshness_at_ms` | INT64 | `0` 表示无期限 |
| `generation_number` | INT64 | 防错过滤 |
| `embedding_model` | VARCHAR(255) | 诊断与对账 |
| `chunker_version` | VARCHAR(64) | 诊断与重建 |
| `content_hash` | VARCHAR(64) | 回读对账 |

Dense 使用 AUTOINDEX/COSINE；Sparse 使用 SPARSE_INVERTED_INDEX/BM25。建表、建索引、load 和 strong-consistency 探针全部成功后，代际才能从 STAGING 转 ACTIVE。

## 7. SQLite 扩展

迁移 `20260728_0004` 扩展现有控制表：

- `curated_versions` 增加 `staging_path`、`published_at`、`claim_set_hash`、`operation_id`。
- `index_generations` 增加 `collection_name`、`embedding_dimension`、`schema_version`、`manifest_hash`。
- `index_jobs` 增加 `content_hash`、`indexed_chunk_count`、`operation_id`、`last_verified_at`。
- 新建 `publication_runs`，保存 Saga 阶段、目标对象、错误分类、尝试次数和审计时间。

新增状态 `PublicationRunStatus`：

~~~text
PLANNING -> CURATING -> VAULT_STAGED -> INDEXING -> INDEX_VERIFIED
         -> VAULT_PUBLISHED -> ACTIVATING -> COMPLETED
任一非终态 -> FAILED
FAILED -> CURATING | VAULT_STAGED | INDEXING | INDEX_VERIFIED | VAULT_PUBLISHED
~~~

恢复目标由已持久化制品决定，不盲目回到开头。`operation_id` 和 `(change_id, target_generation_id)` 唯一，重复命令返回同一 Run。

## 8. 两阶段发布 Saga

1. 校验变更为 `PUBLISH_INTENT`，加载目标 SourceVersion 的可发布 Claim。
2. 查找或创建 KnowledgeNote，冻结规范正式路径。
3. 生成 CurationDraft，保存内容寻址快照和 DRAFT 记录。
4. DRAFT -> VALIDATING；完成 Markdown、Claim 集合、路径与安全验证。
5. 原子写 `_AI/Staging/{note_id}/{curated_version_id}.md`，回读并核对哈希；状态转 STAGING。
6. 在目标 Generation 创建/复用 IndexJob，Chunk、Embedding、幂等 upsert。
7. Strong consistency 按 Chunk ID 回查全部行、哈希和 metadata；Job 转 INDEXED。
8. 正式路径不存在时原子 rename；存在旧版时用 compare-and-swap 检查其 frontmatter 版本与哈希后原子 replace。任何不匹配都失败，绝不覆盖人工修改。
9. 回读正式路径，核对 `curated_version_id` 和 `content_hash`。
10. 单个 SQLite 事务内：新 CuratedVersion -> ACTIVE，Note 指针切换，Job -> ACTIVE_INDEXED，KnowledgeChange -> ACTIVE；旧 CuratedVersion -> SUPERSEDED。
11. 若目标是新全量 Generation，在同一事务中切换唯一 ACTIVE Generation 并将旧代际标记 SUPERSEDED。
12. 写不可变 OperationLog，Run -> COMPLETED。

第 10 步之前，检索只接受旧 SQLite 指针。第 8 步后崩溃时，Reconciler 可根据新文件中已验证的 ID/哈希完成第 10 步；若文件与已验证制品不一致则隔离并保留旧指针。

## 9. 失效、删除与重建

- 修改：旧整理稿可先标记 `STALE_PENDING_REVIEW`，但默认通用检索立即排除；新版本激活后旧版 `SUPERSEDED`。
- 删除：先把 Note 标记删除并禁止 SQLite 当前版本匹配，再创建 `DELETE_PENDING` Job；Milvus 删除是后续清理，不是停止召回的前提。
- 重建：创建新 STAGING Generation，遍历 SQLite 当前 ACTIVE 版本重建完整 Collection，运行结构探针和 Golden Query，达标后原子切换 SQLite ACTIVE 指针。
- 回滚：只允许切回 Schema 和模型配置仍存在、完整校验通过的 SUPERSEDED Generation；不复制或改写旧行。
- 清理：P0 不自动 drop 旧 Collection。只报告可回收代际，删除需单独运维动作。

## 10. Reconciler

对每个当前 Note 检查：

- SQLite 指向的 CuratedVersion 是否 ACTIVE。
- 正式 Vault 文件是否存在，frontmatter ID 和规范化哈希是否一致。
- ACTIVE Generation 中是否存在预期 Chunk 集合，行 metadata 和哈希是否一致。
- 是否存在 SQLite 不引用的正式文件、Milvus Chunk 或未完成 Run。

自动修复仅限幂等且可证明安全的动作：补写缺失索引、重跑回读验证、完成已发布且完全匹配的 SQLite 激活、将无效索引 Job 标记 DELETE_PENDING。文件冲突、未知正式文件、哈希不匹配只报告并隔离，不自动覆盖或删除。

`ReconciliationReport` 返回 `healthy`、`repairable`、`blocked` 三类差异及执行过的动作；错误信息只包含类型、ID 和相对路径，不包含笔记正文或密钥。

## 11. 失败与降级

| 失败 | 行为 |
|---|---|
| Curation 模型不可用/输出非法 | Run 失败；不写 Vault/Milvus |
| Vault Staging 写失败 | Run 失败；旧 ACTIVE 不变 |
| Milvus 不可用 | Run 停在可恢复阶段；旧 ACTIVE 继续服务 |
| Embedding/Reranker 不可用 | 发布不能跳过 Dense；查询可在明确配置下仅用 BM25/RRF 并标记 degraded |
| 正式文件被人工改动 | CAS 失败，报告冲突；不覆盖 |
| SQLite 激活失败 | 新文件/索引保持已验证未激活；Reconciler 完成或隔离 |
| SQLite 控制面不可用 | 检索拒绝返回结果 |
| Milvus 查询失败 | P0 正确拒答；不把 SQLite FTS 当成等价事实检索 |

所有重试有上限，失败类别固定枚举化；日志和 CLI 不输出正文、向量、API Key 或绝对 Vault 路径。

## 12. 配置与运行

新增环境变量：

~~~text
TKB_PUBLICATION_VAULT_PATH=
TKB_PUBLICATION_STAGING_ROOT=_AI/Staging
TKB_PUBLICATION_SNAPSHOT_ROOT=.data/publication-snapshots
TKB_PUBLICATION_PROMPT_VERSION=curation-v1
TKB_PUBLICATION_CHUNKER_VERSION=markdown-v1
TKB_MILVUS_URI=http://localhost:19530
TKB_MILVUS_TOKEN=
TKB_MILVUS_COLLECTION_PREFIX=trustworthy_kb_chunks_g
TKB_MILVUS_CONSISTENCY=Bounded
TKB_EMBEDDING_PROVIDER=bge
TKB_EMBEDDING_MODEL=BAAI/bge-m3
TKB_EMBEDDING_DIMENSION=1024
TKB_RERANKER_PROVIDER=bge
TKB_RERANKER_MODEL=BAAI/bge-reranker-v2-m3
~~~

生产适配器用延迟导入；未安装 `.[retrieval]` 时，非检索模块仍可导入。Compose 文件固定镜像 digest 或版本，数据放命名 volume，不提交模型缓存或索引数据。

## 13. 测试与验收

### 单元/契约

- Curation Schema、允许/禁止状态、敏感级别合并、内容哈希。
- Chunk 边界、稳定 ID、Unicode、超长原子语句和空文档。
- Vault 路径逃逸、symlink、人工冲突、原子写失败和回读哈希。
- Milvus 字段映射、过滤表达式转义、RRF 参数和 consistency。
- 当前版本二次过滤、隐私、时效、质量与降级标记。
- Reconciler 三方差异分类和安全修复白名单。
- 所有 Repository 公共方法、CAS 冲突、唯一约束和迁移升级/降级。

### 集成

- SQLite + 临时 Vault + 内存索引完成全 Saga。
- 在每个 Saga 边界注入崩溃，重跑 operation_id 后恰好一次激活。
- 真实 WSL Milvus：建代际、BM25 + Dense + RRF、Strong 回读、删除和回滚。
- 真实 BGE 只作为显式慢速集成，不进入默认 CI。

### L4 完成门

1. 半发布内容在任何故障点都不会通过默认检索。
2. 旧 Chunk 和错误 curated_version_id 的命中率为 0。
3. 相同发布操作重放不会生成重复版本、Job 或 Chunk。
4. 新代际验证失败时旧代际保持 ACTIVE。
5. Vault 人工修改不会被自动覆盖。
6. `ruff`、`mypy`、默认 pytest 与覆盖率门通过。
7. WSL Milvus 实际混合检索集成通过。

## 14. 参考

- [Milvus Standalone 安装要求](https://milvus.io/docs/v2.6.x/prerequisite-docker.md)
- [Milvus Docker Compose](https://milvus.io/docs/v2.6.x/install_standalone-docker-compose.md)
- [Milvus BM25 Function](https://milvus.io/docs/bm25-function.md)
- [Milvus RRF Ranker](https://milvus.io/docs/v2.6.x/rrf-ranker.md)
- [Milvus Consistency](https://milvus.io/docs/consistency.md)
- [BAAI/bge-m3 模型卡](https://huggingface.co/BAAI/bge-m3)
