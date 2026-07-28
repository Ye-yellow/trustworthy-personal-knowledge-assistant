# L1 领域模型与 SQLite 控制面设计

> 状态：已确认，待实施
>
> 日期：2026-07-28
>
> 范围：P0 的 L1 领域契约、SQLite Schema、迁移、Repository、事务、审计与幂等基础

## 1. 结论

L1 采用“薄 ORM + 纯领域规则 + Repository/Unit of Work”架构。SQLAlchemy ORM 只负责持久化映射，Pydantic 模型构成稳定领域契约，状态转换由无数据库依赖的纯函数控制。异步 Repository 隐藏 SQLAlchemy 查询，`SqliteUnitOfWork` 是唯一提交和回滚边界。

首批迁移一次建立完整控制面骨架，使 L2-L4 可以在稳定 Schema 上逐层实现采集、Claim 治理和发布检索，而不在 L1 提前实现这些业务流程。

## 2. 已确认选择

| 主题 | 选择 |
|---|---|
| Schema 范围 | 完整控制骨架 |
| 数据访问 | SQLAlchemy 2.0 Async + aiosqlite |
| 迁移 | Alembic 异步模板 |
| ID | 带对象类型前缀的单调 ULID |
| 删除 | 默认逻辑删除；版本、裁决和审计保留历史 |
| 架构 | 薄 ORM、纯领域规则、Repository、显式 Unit of Work |
| 并发 | revision compare-and-swap + SQLite 单写者约束 |
| 幂等 | 唯一 key、请求哈希、lease、结果引用 |

## 3. 范围与非目标

### 3.1 L1 交付

- 领域 ID、枚举、Pydantic 契约和状态转换。
- 完整 SQLite 控制面表、外键、唯一约束、检查约束和索引。
- SQLAlchemy 2.0 异步 Engine、Session 和 SQLite PRAGMA。
- 按职责拆分的异步 Repository。
- 显式提交、异常回滚的 Unit of Work。
- 乐观并发、操作日志和幂等操作契约。
- Alembic 初始迁移、Schema head 检查和迁移测试。
- 公共接口、失败分支、并发和隐私测试。

### 3.2 L1 非目标

- 不读取或写入 Obsidian Vault。
- 不解析附件、计算正文 Diff 或实现不可变文件存储。
- 不调用 LLM 抽取或验证 Claim。
- 不实现 LangGraph 工作流、任务队列或 Checkpoint Adapter。
- 不连接 Milvus，不创建 Chunk 或向量。
- 不实现两阶段发布 Saga 或 Reconciler。
- 不实现 FastAPI、SSE、问答或评估。

`graph_checkpoints` 由 L2 的 LangGraph Adapter 管理，不进入 L1 初始迁移。

## 4. 依赖与模块边界

核心依赖新增：

- `sqlalchemy>=2.0,<2.1`
- `aiosqlite`
- `alembic>=1.18,<2`
- `python-ulid`

依赖版本由 `uv.lock` 固定。`python-ulid` 提供 Python 3.12、单调 ULID 和 Pydantic 支持。Alembic 使用官方异步配置，通过 SQLAlchemy AsyncEngine 执行迁移。

目录结构保持浅层：

~~~text
src/trustworthy_kb/
  config/
    database.py
  domain/
    __init__.py
    ids.py
    enums.py
    errors.py
    transitions.py
    source.py
    knowledge.py
    publication.py
    audit.py
  persistence/
    __init__.py
    database.py
    base.py
    types.py
    source_tables.py
    knowledge_tables.py
    publication_tables.py
    audit_tables.py
    source_repository.py
    knowledge_repository.py
    publication_repository.py
    audit_repository.py
    unit_of_work.py
migrations/
  env.py
  versions/
alembic.ini
tests/
  domain/
  persistence/
  migrations/
~~~

依赖方向固定为：

~~~text
业务层
  → domain 契约 / persistence Repository
  → SqliteUnitOfWork
  → SQLAlchemy AsyncSession
  → SQLite
~~~

ORM Table 不作为公共领域 API。每个包通过显式 `__all__` 暴露稳定接口，业务代码不导入 SQLAlchemy Table 类。

## 5. ID、时间与公共类型

### 5.1 前缀 ULID

ID 使用 `{prefix}_{26-character-ulid}` 格式，ULID 以大写 Crockford Base32 保存。首批前缀：

| 对象 | 前缀 |
|---|---|
| Source | `source_` |
| SourceVersion | `srcver_` |
| ContentBlock | `block_` |
| Claim | `claim_` |
| EvidenceFamily | `evfam_` |
| Evidence | `evidence_` |
| QualityCheck | `qcheck_` |
| KnowledgeChange | `change_` |
| KnowledgeNote | `note_` |
| CuratedVersion | `curated_` |
| LineageEdge | `lineage_` |
| IndexGeneration | `idxgen_` |
| IndexJob | `idxjob_` |
| ModelRun | `modelrun_` |
| OperationLog | `oplog_` |
| IdempotencyRecord | `idem_` |

`domain.ids` 为每种 ID 提供不可变字符串类型、生成器和严格解析函数。Pydantic 校验前缀、长度和 ULID；SQLite 对主键增加前缀 `CHECK`。业务代码不能接受无类型的随机字符串作为领域 ID。

### 5.2 时间

- 所有持久化时间使用规范 UTC 文本：`YYYY-MM-DDTHH:MM:SS.ffffffZ`。
- `UTCDateTime` SQLAlchemy TypeDecorator 拒绝无时区 `datetime`，写入前转换为 UTC，读取后返回 UTC-aware `datetime`。
- 来源声明时间与系统采集时间使用不同字段，不能用本机时间覆盖来源时间。

### 5.3 JSON 与哈希

- JSON 列使用 SQLAlchemy JSON 映射，并增加 SQLite `json_valid` 检查。
- 所有 JSON 在写审计哈希前使用稳定键排序和紧凑序列化。
- 内容和请求哈希使用小写 64 位 SHA-256 十六进制字符串，并增加长度/字符检查。
- 控制数据库不保存原始正文、完整 Prompt、完整模型响应或 API key。

## 6. 状态模型

### 6.1 SourceVersionStatus

~~~text
CAPTURED → PARSED → READY
CAPTURED/PARSED → PARSE_FAILED
PARSE_FAILED → PARSED
CAPTURED/PARSED/PARSE_FAILED → QUARANTINED
CAPTURED/PARSED/READY/PARSE_FAILED/QUARANTINED → DELETED
~~~

`PARSE_FAILED → PARSED` 只表示对同一不可变版本重新解析成功，不允许修改版本正文身份或内容哈希。

### 6.2 ClaimStatus

~~~text
PROPOSED → VERIFIED | USER_ASSERTED | OPINION | INSUFFICIENT | CONTESTED | REJECTED
VERIFIED | USER_ASSERTED | OPINION | INSUFFICIENT | CONTESTED → OUTDATED | SUPERSEDED
~~~

`human_override` 是裁决元数据，不是 Claim 状态，不能把 `CONTESTED` 或 `INSUFFICIENT` 自动改为 `VERIFIED`。

### 6.3 CuratedVersionStatus

~~~text
DRAFT → VALIDATING → STAGING → ACTIVE
ACTIVE → STALE_PENDING_REVIEW | SUPERSEDED
VALIDATING | STAGING → QUARANTINED | FAILED
~~~

### 6.4 IndexGenerationStatus 与 IndexJobStatus

~~~text
IndexGeneration: STAGING → ACTIVE → SUPERSEDED
IndexGeneration: STAGING → FAILED

IndexJob: PENDING → INDEXING → INDEXED → ACTIVE_INDEXED
IndexJob: INDEXING → FAILED
IndexJob: FAILED → PENDING
IndexJob: ACTIVE_INDEXED → DELETE_PENDING → DELETED
~~~

`FAILED → PENDING` 必须由显式有界重试触发，同时增加 attempt 并清理旧 lease；达到最大尝试次数后保持 `FAILED`。

### 6.5 KnowledgeChangeStatus

~~~text
RECEIVED → VALIDATING → PUBLISH_INTENT → ACTIVE
RECEIVED | VALIDATING | PUBLISH_INTENT → FAILED | QUARANTINED
~~~

### 6.6 ModelRunStatus 与 IdempotencyStatus

~~~text
ModelRun: STARTED → SUCCEEDED | FAILED | CANCELLED
Idempotency: IN_PROGRESS → SUCCEEDED | FAILED | UNKNOWN
Idempotency: UNKNOWN → SUCCEEDED | FAILED
~~~

普通调用方不能执行 `UNKNOWN` 转换；只有 Reconciler 在查询外部真实状态后可以将其解析为 `SUCCEEDED` 或 `FAILED`。

转换规则由 `domain.transitions` 中的显式邻接表定义。`transition(current, target)` 返回目标状态或抛出 `InvalidStateTransitionError`，不访问数据库。

## 7. SQLite Schema

### 7.1 通用约定

- 所有表使用明确命名的主键、外键、唯一约束、检查约束和索引。
- 外键默认 `ON DELETE RESTRICT`，不使用级联物理删除。
- 可变记录包含 `revision INTEGER NOT NULL DEFAULT 1`。
- 可逻辑删除记录包含 `deleted_at`，默认查询排除已删除对象。
- 数据库枚举以字符串存储并由 `CHECK` 限制为已知值。
- 状态改变只能通过 Repository 意图方法和 revision compare-and-swap。

### 7.2 来源链

#### `sources`

- `id`、`source_type`、`canonical_uri`、`owner`
- `trust_tier`、`sensitivity`
- `current_version_id`，可空，指向 `source_versions`
- `revision`、`created_at`、`updated_at`、`deleted_at`

唯一约束：`(source_type, canonical_uri, owner)`。`current_version_id` 必须属于同一 Source 且为 `READY`，该跨表不变量由 Repository 在事务中验证。

#### `source_versions`

- `id`、`source_id`、`version_number`
- `content_hash`、`byte_size`、`media_type`
- `captured_at`、`source_modified_at`、`original_path`
- `status`、`revision`、`created_at`、`updated_at`

唯一约束：`(source_id, version_number)` 和 `(source_id, content_hash)`。正文身份字段在创建后不可修改；只有状态、revision 和更新时间可按状态机改变。

#### `content_blocks`

- `id`、`source_version_id`、`ordinal`
- `block_type`、`anchor`、`text_hash`、`character_count`
- `created_at`

唯一约束：`(source_version_id, ordinal)` 和 `(source_version_id, anchor)`。记录只保存定位和哈希，不保存正文。

### 7.3 Claim、证据与裁决

#### `claims`

- `id`、`claim_type`、`subject`、`predicate`、`object_json`
- `scope_json`、`valid_from`、`valid_to`、`freshness_at`
- `sensitivity`、`status`、`current_quality_check_id`
- `superseded_by_id`、`revision`、`created_at`、`updated_at`、`deleted_at`

`current_quality_check_id` 必须属于同一 Claim。`superseded_by_id` 不能等于自身。

#### `claim_origins`

- `claim_id`、`content_block_id`
- `model_run_id`，可空
- `origin_span_json`、`created_at`

复合主键：`(claim_id, content_block_id)`。ModelRun 为空表示用户输入或确定性转换。

#### `evidence_families`

- `id`、`canonical_origin`、`origin_fingerprint`
- `created_at`

`origin_fingerprint` 唯一，用于防止转载和近重复来源被当作独立证据。

#### `evidence`

- `id`、`claim_id`、`source_version_id`、`evidence_family_id`
- `anchor`、`stance`、`excerpt_hash`
- `relevance_score`、`independence_score`、`created_at`

唯一约束：`(claim_id, source_version_id, anchor, stance)`。`stance` 限制为 `SUPPORTS`、`CONTRADICTS` 或 `NEUTRAL`。

#### `quality_checks`

- `id`、`claim_id`、`policy_version`、`verdict`
- `dimensions_json`、`reason_code`、`reason_summary`
- `evidence_snapshot_hash`、`model_run_id`、`human_override`
- `created_at`

QualityCheck 创建后禁止更新或删除。`reason_summary` 只能保存脱敏摘要，不保存完整证据正文。

#### `quality_check_evidence`

- `quality_check_id`、`evidence_id`、`position`

复合主键：`(quality_check_id, evidence_id)`；`(quality_check_id, position)` 唯一，用于固化一次裁决实际使用的证据顺序。

### 7.4 整理、血缘与索引控制

#### `knowledge_changes`

- `id`、`source_id`、`base_version_id`、`target_version_id`
- `change_type`、`diff_hash`、`diff_summary_json`
- `status`、`operation_id`、`revision`
- `created_at`、`updated_at`

`target_version_id` 必须属于 `source_id`；`base_version_id` 可空，非空时也必须属于同一 Source。

#### `knowledge_notes`

- `id`、`canonical_path`
- `current_curated_version_id`、`active_index_generation_id`
- `revision`、`created_at`、`updated_at`、`deleted_at`

`canonical_path` 对未删除 Note 唯一。当前整理版本必须属于同一 Note 且为 `ACTIVE`。

#### `curated_versions`

- `id`、`note_id`、`version_number`
- `based_on_change_id`、`content_hash`、`vault_path`
- `status`、`revision`、`created_at`、`updated_at`

唯一约束：`(note_id, version_number)` 和 `(note_id, content_hash)`。内容身份字段不可修改，状态按状态机变化。

#### `lineage_edges`

- `id`、`from_type`、`from_id`
- `to_type`、`to_id`、`relation`
- `operation_id`、`created_at`

唯一约束：`(from_type, from_id, to_type, to_id, relation)`。数据库检查对象类型与 ID 前缀一致；Repository 验证对象存在。LineageEdge 创建后禁止更新或删除。

#### `index_generations`

- `id`、`generation_number`
- `embedding_model`、`chunker_version`
- `status`、`revision`、`created_at`、`activated_at`

`generation_number` 唯一。P0 使用单一知识库，部分唯一索引保证最多一个 `ACTIVE` Generation。

#### `index_jobs`

- `id`、`object_type`、`object_id`、`generation_id`
- `status`、`attempt`、`error_category`
- `lease_owner`、`lease_expires_at`
- `revision`、`created_at`、`updated_at`

唯一约束：`(object_type, object_id, generation_id)`。错误字段只保存稳定类别，不保存异常正文。

### 7.5 模型运行、操作日志与幂等

#### `model_runs`

- `id`、`purpose`、`provider`、`model`、`prompt_version`
- `status`、`input_hash`、`output_hash`
- `input_tokens`、`output_tokens`、`total_tokens`
- `latency_ms`、`request_id`、`error_category`
- `started_at`、`completed_at`、`revision`

不保存 Prompt、模型原始输入或完整响应。身份和版本字段不可修改，生命周期字段通过状态转换更新。

#### `operation_logs`

- `id`、`operation_id`、`step_number`
- `actor_type`、`actor_id`、`action`
- `target_type`、`target_id`
- `before_json`、`after_json`
- `previous_entry_hash`、`entry_hash`、`created_at`

唯一约束：`(operation_id, step_number)` 和 `entry_hash`。每个 operation 构成独立哈希链。数据库触发器拒绝 UPDATE 和 DELETE。JSON 只记录状态、版本、哈希和 ID 等允许字段。

#### `idempotency_records`

- `id`、`scope`、`idempotency_key`、`request_hash`
- `status`、`result_type`、`result_id`
- `lease_owner`、`lease_expires_at`
- `attempt`、`error_category`
- `revision`、`created_at`、`updated_at`、`expires_at`

唯一约束：`(scope, idempotency_key)`。key 不允许包含凭据或私人正文。

## 8. Repository 契约

Repository 使用业务意图方法，不提供任意字段更新：

### 8.1 SourceRepository

- `add_source`
- `get_source`
- `append_source_version`
- `add_content_blocks`
- `transition_source_version`
- `activate_source_version`
- `mark_source_deleted`

### 8.2 KnowledgeRepository

- `add_claim`
- `attach_claim_origin`
- `add_evidence_family`
- `add_evidence`
- `record_quality_check`
- `transition_claim`
- `set_current_quality_check`
- `mark_claim_deleted`

### 8.3 PublicationRepository

- `add_knowledge_change`
- `transition_knowledge_change`
- `add_note`
- `add_curated_version`
- `transition_curated_version`
- `activate_curated_version`
- `add_lineage_edge`
- `add_index_generation`
- `transition_index_generation`
- `add_index_job`
- `transition_index_job`

### 8.4 AuditRepository

- `start_model_run`
- `finish_model_run`
- `append_operation_log`
- `acquire_idempotency_key`
- `complete_idempotent_operation`
- `fail_idempotent_operation`
- `mark_idempotent_operation_unknown`

所有返回值是冻结的 Pydantic 领域记录。Repository 只执行 `flush()`，不能 `commit()`。

## 9. Unit of Work 与并发

`SqliteUnitOfWork` 作为异步上下文管理器：

~~~python
async with unit_of_work_factory() as uow:
    source = await uow.sources.get_source(source_id)
    await uow.sources.activate_source_version(source.id, version.id, source.revision)
    await uow.audit.append_operation_log(...)
    await uow.commit()
~~~

- `commit()` 必须显式调用。
- 未调用 commit 的正常退出执行 rollback。
- 异常退出执行 rollback，并保留原项目异常类型。
- 每个状态更新包含 `WHERE id = :id AND revision = :expected_revision`。
- 受影响行数不是 1 时抛出 `ConcurrentModificationError`。
- 多表不变量在同一写事务内查询和验证。
- 事务保持短小，不在事务内调用 LLM、HTTP、Obsidian 或 Milvus。

SQLite 连接统一启用：

- `PRAGMA foreign_keys=ON`
- `PRAGMA journal_mode=WAL`
- `PRAGMA synchronous=NORMAL`
- `PRAGMA busy_timeout=5000`

锁超时映射为 `DatabaseBusyError`。L1 不在 Unit of Work 内自动重放完整事务；上层必须使用幂等 operation 明确决定是否重试。

## 10. 幂等协议

1. 调用方提供不含私人内容的 `scope`、`idempotency_key` 和稳定 `request_hash`。
2. Repository 依靠 `(scope, idempotency_key)` 唯一约束尝试创建 `IN_PROGRESS` 记录。
3. 相同 key、不同请求哈希抛出 `IdempotencyConflictError`。
4. 已 `SUCCEEDED` 时直接返回原 `result_type/result_id`。
5. lease 有效的 `IN_PROGRESS` 返回 `OperationInProgressError`。
6. lease 过期时通过 revision compare-and-swap 接管，增加 attempt 并更新 owner。
7. 完成时将结果引用、幂等状态和操作日志与领域变更一起提交。
8. 无法确认外部副作用时写 `UNKNOWN`；`UNKNOWN` 不自动重试，交由后续 Reconciler 查询真实状态。

## 11. 数据库错误与安全失败

公共错误类型：

- `PersistenceError`
- `DatabaseConfigurationError`
- `DatabaseSchemaMismatchError`
- `DatabaseBusyError`
- `RecordNotFoundError`
- `DuplicateRecordError`
- `ConcurrentModificationError`
- `InvalidStateTransitionError`
- `InvariantViolationError`
- `IdempotencyConflictError`
- `OperationInProgressError`

异常消息只能包含对象类型、脱敏对象 ID、错误类别和安全状态。底层 SQL、绑定参数、正文、JSON 内容、数据库 URL 和凭据不能进入公共异常或日志。

## 12. 配置与迁移

新增 `DatabaseSettings`：

- `TRUSTKB_DATABASE_URL`，默认 `sqlite+aiosqlite:///./data/trustworthy_kb.db`
- `TRUSTKB_DATABASE_BUSY_TIMEOUT_MS`，默认 `5000`

只接受 `sqlite+aiosqlite` URL。数据库 URL 使用 SecretStr 风格脱敏显示，避免未来路径或查询参数进入日志。

迁移规则：

- 使用 Alembic `pyproject_async` 或等价异步模板。
- 首个 migration revision 创建全部 L1 表、索引、约束和操作日志触发器。
- `uv run alembic upgrade head` 是唯一初始化方式。
- 运行时不调用 `metadata.create_all()`，也不静默迁移。
- 应用启动通过 Alembic MigrationContext 比较 current head；不一致时抛出 `DatabaseSchemaMismatchError` 并提示迁移命令。
- migration downgrade 只用于开发与测试；生产数据恢复依靠备份和前向迁移。

## 13. 测试策略

### 13.1 Unit

- 每种 ID 的生成、解析、错误前缀和排序。
- 每个枚举的全部合法和非法状态转换。
- UTCDateTime 拒绝无时区值并规范化 UTC。
- 领域 Pydantic 契约的边界和错误输入。
- 操作日志规范 JSON 和哈希链计算。

### 13.2 Repository 与事务

- 所有公共 Repository 方法的成功、未找到、重复和约束错误。
- 外键、唯一约束、枚举 CHECK 和 JSON CHECK。
- 显式 commit 持久化；未 commit 和异常均回滚。
- 领域写入、指针切换、幂等结果和操作日志原子提交。
- 陈旧 revision 更新失败且不产生部分写入。
- 逻辑删除默认过滤，显式历史查询可读取。
- OperationLog UPDATE/DELETE 被触发器拒绝。

### 13.3 并发与幂等

- 相同 key、相同 hash 最多一个调用者获得执行权。
- 相同 key、不同 hash 必须冲突。
- 成功结果可重放。
- lease 未过期不可接管，过期后只能一个调用者接管。
- `UNKNOWN` 不被普通 acquire 自动重试。

### 13.4 Migration 与 Contract

- 临时文件数据库从空库 `upgrade head`。
- `downgrade base` 后可再次升级。
- 数据库表、索引、外键、触发器与 ORM metadata 一致。
- PRAGMA 在每个连接生效。
- current revision 与 Alembic head 一致。

### 13.5 质量门

- Ruff、格式、严格 mypy、隐私扫描和 wheel/sdist 构建通过。
- 默认测试不读取真实 Vault、真实模型 key 或本机数据库。
- 新增代码覆盖率不低于 80%，完整项目覆盖率不低于 80%。

## 14. 验收标准

1. 空目录可通过 `uv run alembic upgrade head` 创建完整 L1 控制数据库。
2. ORM Table 不出现在 `domain` 公共 API，业务层只消费领域记录与 Repository。
3. 非法状态转换不会产生领域写入或审计缺口。
4. 同一幂等 key 的并发请求最多一个获得执行权。
5. 任意事务失败时，领域表、活动指针、幂等结果和操作日志一起回滚。
6. 已存在对象的陈旧 revision 更新稳定返回并发冲突。
7. SourceVersion、QualityCheck、LineageEdge 和 OperationLog 的不可变部分不能被普通 Repository 修改。
8. 默认删除只写 `deleted_at`，外键血缘不会级联消失。
9. 数据库错误和日志不包含 SQL 参数、正文、私密 JSON 或数据库 URL。
10. Alembic head、离线测试、覆盖率、静态检查、隐私扫描、构建和 CI 全部通过。

## 15. 实施顺序

1. 依赖、DatabaseSettings、ID、枚举、错误和状态转换测试。
2. SQLAlchemy Base、自定义类型、Engine、Session 与 PRAGMA 测试。
3. 四组 Table、约束、索引和 ORM contract 测试。
4. Alembic 环境、初始 migration 和 upgrade/downgrade 测试。
5. SourceRepository 与 KnowledgeRepository。
6. PublicationRepository 与 AuditRepository。
7. Unit of Work、并发和幂等测试。
8. Schema head 检查、README、隐私扫描、完整质量门和 CI。

## 16. 参考资料

- [SQLAlchemy 2.0 SQLite Dialect](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html)
- [Alembic Asyncio Cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)
- [Alembic Templates](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [python-ulid](https://pypi.org/project/python-ulid/)
