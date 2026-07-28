# L2 可信采集、不可变快照与运行恢复设计

> 日期：2026-07-28
> 状态：设计章节已确认，待书面复核与实施
> 范围：手动全量扫描、Obsidian 只读 Adapter、内容寻址快照、Markdown 结构解析、变化规划、采集账本、幂等应用与 LangGraph SQLite 恢复

## 1. 决策摘要

L2 采用“确定性 scan → plan → apply 核心 + 薄恢复适配器”。采集核心不依赖 LangGraph、Obsidian GUI 或 LLM；相同输入必须生成相同 manifest、变化计划、结构块和 Diff。LangGraph 仅负责编排与 checkpoint，任务重放仍由 L1 幂等协议保证安全。

首版只支持用户显式触发的单 Vault Markdown 全量扫描。生产 inventory 通过官方 Obsidian CLI 获取，原始字节通过严格路径策略从本地 Vault 只读获取，以保留 BOM、换行和 Unicode 原貌。测试使用合成 Vault Adapter，不启动 Obsidian，不读取真实个人资料。

不可变快照保存在 Vault 外的内容寻址目录。SQLite 只保存路径、哈希、结构、状态、错误类别和血缘，不保存 Markdown 正文。checkpoint 使用独立 SQLite 文件，避免污染 Alembic 控制面或放大同一数据库的锁竞争。

## 2. 目标

- 扫描一个配置明确的 Obsidian Vault，并生成稳定 manifest。
- 对原始 Markdown 执行稳定读取、大小限制、UTF-8 检查和 SHA-256。
- 在 Vault 外保存只写、可校验、可复用的内容寻址快照。
- 将 Markdown 确定性拆成带稳定锚点的 ContentBlock。
- 识别 CREATED、UPDATED、MOVED、DELETED 和 UNCHANGED。
- 将 Source、SourceVersion、ContentBlock、KnowledgeChange、位置、账本、幂等和审计原子写入。
- 扫描或进程中断后可重跑或从 LangGraph checkpoint 恢复，不产生重复版本或部分激活。
- 所有错误、日志和审计不包含正文、完整私人路径、CLI 原始输出或凭据。

## 3. 非目标

- 不实现文件 watcher、实时事件或多 worker 并行消费。
- 不修改、移动、创建或删除真实 Vault 文件。
- 不解析附件、PDF、图片、OCR、Canvas 或 Base 文件。
- 不执行 Claim 抽取、证据检索、LLM 调用或质量裁决。
- 不写回 Properties、Tags、Wikilink 或整理稿。
- 不处理多个 Vault 之间的复制或移动。
- 不清理无引用快照；L2 只保证无引用快照安全无害。
- 不把真实 Vault、快照、checkpoint 或控制数据库提交到 Git。

## 4. 已选方案与备选方案

### 4.1 已选：确定性扫描核心

扫描、读取、解析、规划和单项应用均通过小型 Protocol 与纯数据契约通信。核心可以在无 Obsidian、无 LangGraph 的测试中完整运行。LangGraph task 调用同一个单项应用服务，不复制业务规则。

优点：可测试、可重跑、框架耦合低；对 L1 Repository 和 Unit of Work 的复用直接。缺点：需要额外维护少量编排 Adapter。

### 4.2 未选：LangGraph-first

把 inventory、读取、解析和写库全部实现为图节点。恢复接口统一，但确定性函数也被 checkpoint 状态格式绑定，简单单元测试和未来替换编排器更困难。

### 4.3 未选：watcher/event-first

文件变化即时入队，但必须先解决事件丢失、重复、乱序、稳定窗口和 move+modify 竞态。首版全量扫描已经能作为 watcher 的最终对账机制，因此 watcher 延后。

## 5. 模块边界

~~~text
trustworthy_kb/
  config/
    ingestion.py
  domain/
    ingestion.py
  ingestion/
    adapters/
      obsidian_cli.py
      filesystem_reader.py
    hashing.py
    markdown.py
    manifest.py
    planner.py
    safety.py
    service.py
    snapshots.py
    workflow.py
  persistence/
    ingestion_tables.py
    ingestion_repository.py
    unit_of_work.py
migrations/
  versions/
tests/
  ingestion/
  persistence/
  integration/
~~~

### 5.1 `VaultInventory` Protocol

职责：列出允许范围内的 Markdown，相对路径统一使用 `/`，并提供 size、mtime_ns 和可选 file_key。

生产实现调用官方 CLI：

- `obsidian vault=<id> files ext=md`
- `obsidian vault=<id> file path=<relative-path>`

所有命令通过 `asyncio.create_subprocess_exec` 或等价的参数数组执行，禁止 shell 拼接。Vault 必须由显式 ID 指定，不能依赖当前活动 Vault。CLI 超时、缺失、非零退出和超量输出映射为稳定错误类别，不能把 stdout/stderr 放入公共异常。

只有 CLI 命令完整成功、全部输出通过解析和范围校验后，inventory 才标记为 complete。任何截断、超时、重复 path_key、非法路径或中途失败都会让本次 run 在规划前失败；不允许拿部分 inventory 推导 DELETED。

### 5.2 `StableMarkdownReader`

职责：在本地文件系统读取原始字节。它不是 Vault 写入 Adapter。

规则：

1. 将相对路径按 POSIX 语义规范化，拒绝绝对路径、`..`、NUL 和保留设备名。
2. resolve 后验证目标仍位于配置的 Vault 根目录。
3. 拒绝符号链接、目录和非 `.md` 文件。
4. 读取前后采集 size、mtime_ns、file_key，并对字节计算 SHA-256。file_key 仅在平台返回非零且 Adapter 判定可靠时可用，持久化值是 `SHA256(st_dev || ":" || st_ino)`，不保存原始 inode/device。
5. 两次观察不一致时按配置间隔重试；超过次数抛出 `UnstableFileError`。
6. 原始快照保留 BOM 和换行；解析副本只接受 UTF-8 或 UTF-8 BOM。

### 5.3 `ContentAddressedSnapshotStore`

布局：

~~~text
<snapshot_root>/sha256/<first-two>/<remaining-62>.md
~~~

- 输入文件名只来自验证过的 64 位小写十六进制哈希。
- 先在同一目标目录创建临时文件，flush/fsync 后原子 rename。
- 目标已存在时重新计算哈希；一致则复用，不一致立即抛出 `SnapshotIntegrityError`。
- API 返回 `SnapshotRef(content_hash, byte_size)`，不向领域层暴露本机绝对路径。
- 快照目录必须位于 Vault 外；配置校验拒绝两者重叠、父子包含或同一路径。

### 5.4 `MarkdownBlockParser`

解析输入是 UTF-8 文本，输出只在内存包含规范化文本；持久化 ContentBlock 只保存锚点、类型、哈希和字符数。

块类型：

- `frontmatter`
- `heading`
- `paragraph`
- `list`
- `blockquote`
- `table`
- `code_fence`
- `thematic_break`

锚点由 heading 层级路径、块类型和同类序号构成。标题 slug 使用 Unicode NFC、trim、空白折叠和小写，不删除非 ASCII 字符；重复标题附加序号。显式 Obsidian block ID `^id` 可作为局部锚点，但不能单独作为全 Vault 身份。

SourceVersion `content_hash` 基于原始字节；ContentBlock `text_hash` 基于 Unicode NFC、LF 换行和去除行尾空白后的块文本。解析器不改写快照。

实现选用 `markdown-it-py` 的 CommonMark token 流和 `PyYAML.safe_load`；依赖由 uv lock 固定。Frontmatter 必须存在成对 `---` 边界并能被安全 YAML 解析。解析前额外检查未闭合代码围栏；损坏 frontmatter、未闭合代码围栏和非法 UTF-8 产生确定性解析失败，不猜测修复。

### 5.5 `DocumentSafetyScanner`

L2 只提供确定性安全信号，不作事实裁决。扫描结果只保存类别和计数，不保存命中内容：

- 私钥边界和常见长 Token 形态。
- Unicode bidi/零宽控制字符。
- 超长可疑编码块。
- 高置信“忽略系统规则/执行工具”等指令注入模式。

高置信 Secret 或主动指令信号使 SourceVersion 进入 QUARANTINED，不激活为 current_version；低置信信号只以类别与计数写入 ingestion item 的 `safety_signals_json`，留给 L3 质量门。

### 5.6 `IngestionPlanner`

输入：complete manifest、SQLite 中该 Vault 的 live Source/Location/current 与 latest SourceVersion 摘要。输出：按 path_key、action 排序的冻结 `IngestionPlan`。

匹配优先级：

1. 相同 Vault + path_key。
2. 唯一且可靠的 file_key。
3. 旧端唯一缺失 Source 与新端唯一文件具有相同 raw content_hash。

结果：

- 路径匹配且哈希相同：UNCHANGED。
- 路径匹配且哈希变化：UPDATED。
- file_key 或唯一哈希匹配且路径变化：MOVED。
- 无匹配新文件：CREATED。
- 无匹配旧 Source：DELETED。

删除候选只来自本次 allowed/excluded roots 明确定义的扫描范围，并且只有 inventory complete 时才会生成；配置缩小后落在范围外的历史 Source 不是删除候选。读取失败但仍存在于 complete inventory 的文件也不是删除候选，而是失败 item。

如果文件同时移动并修改且无可靠 file_key，必须退化为 DELETED + CREATED。多个候选共享哈希时禁止猜测 move。path 匹配但没有 current version 时使用 latest version 比较：相同 PARSE_FAILED 内容生成可重试的 UPDATED；相同 QUARANTINED 内容保持隔离且不自动重试。

### 5.7 `IngestionService`

职责：把一个计划项应用到 L1 控制面。外部读取、快照和解析都在事务外完成；数据库事务只执行短写入。

### 5.8 `IngestionWorkflow`

LangGraph 图固定为：

~~~text
START → inventory → stable_read_and_snapshot → plan
      → apply_items → reconcile_run → END
~~~

`apply_items` 的每个 item 是独立 task。Graph State 只包含 run ID、item ID、path_key、内容哈希、动作、计数和稳定错误类别，不包含 Markdown 正文、相对/绝对路径、快照字节或凭据。task 需要路径时按 item ID 从私有 SQLite 读取。

## 6. 配置

新增 `IngestionSettings`，环境变量前缀 `TRUSTKB_INGESTION_`：

| 字段 | 默认/规则 |
|---|---|
| `vault_id` | 必填，非空；用于 CLI 定位，显示时脱敏 |
| `vault_path` | 必填，本地已存在目录；SecretStr 风格显示 |
| `snapshot_root` | 必填或默认 `./storage/source-snapshots`；必须在 Vault 外 |
| `checkpoint_path` | 默认 `./storage/checkpoints/ingestion.sqlite` |
| `obsidian_executable` | 默认 `obsidian` |
| `allowed_roots` | 默认整个 Vault，可缩小到 Inbox/项目目录 |
| `excluded_roots` | 默认 `.obsidian`、`.trash`、`_AI`、附件和配置目录 |
| `max_markdown_bytes` | 默认 5 MiB，范围 1 KiB–50 MiB |
| `stable_read_interval_ms` | 默认 250，范围 50–5000 |
| `stable_read_attempts` | 默认 3，范围 1–10 |
| `cli_timeout_seconds` | 默认 30，范围 1–300 |
| `cli_output_limit_bytes` | 默认 4 MiB |

Vault ID、Vault path、snapshot path 和 checkpoint path 不进入异常 repr 或日志。数据库保存 `vault_id_hash = SHA256(vault_id)`，不保存 CLI Vault ID 原文。

扫描范围以 canonical JSON 的 `allowed_roots`、`excluded_roots` 和规则版本计算 `scan_scope_hash`。Source 的 canonical URI 固定为 `obsidian://vault/<vault_id_hash>/<逐段百分号编码的相对路径>`；URI 构造只能接收已通过路径策略的相对路径。

## 7. 领域契约

### 7.1 新 ID

| 类型 | 前缀 |
|---|---|
| IngestionRunId | `ingrun_` |
| IngestionItemId | `ingitem_` |

继续使用单调 ULID 和严格前缀解析。

### 7.2 枚举

`IngestionAction`：CREATED、UPDATED、MOVED、DELETED、UNCHANGED。

`IngestionRunStatus`：PLANNING、APPLYING、COMPLETED、PARTIAL_FAILED、FAILED、ABANDONED。

`IngestionItemStatus`：PENDING、APPLYING、SUCCEEDED、SKIPPED、QUARANTINED、FAILED。

状态转换：

- Run：PLANNING → APPLYING → COMPLETED/PARTIAL_FAILED/FAILED；PLANNING/APPLYING → ABANDONED。
- Item：PENDING → APPLYING → SUCCEEDED/SKIPPED/QUARANTINED/FAILED；FAILED → PENDING 仅显式重试。

### 7.3 内存契约

- `VaultFileObservation`
- `StableDocument`
- `ParsedBlock`
- `ParsedDocument`
- `SafetySignal`
- `IngestionPlanItem`
- `IngestionPlan`
- `IngestionReport`

这些模型冻结、禁止 extra。含正文/字节的模型只存在于 ingestion 内部模块，不从 `trustworthy_kb.domain` 公共 API 导出。

## 8. Schema 与迁移

第二个 Alembic revision 创建三张表。

### 8.1 `source_locations`

- `source_id`，主键和 `sources.id` RESTRICT 外键。
- `vault_id_hash`、`relative_path`、`path_key`。
- `file_key`，可空；只有 Adapter 声明可靠时用于 move。
- `last_seen_run_id`，可空，指向 ingestion_runs。
- `observed_size`、`observed_mtime_ns`。
- `deleted_at`，可空；Source 逻辑删除时同步设置。
- `revision`、`created_at`、`updated_at`。

`source_id` 每个 Source 最多一条位置记录；另建 live partial unique index `(vault_id_hash, path_key) WHERE deleted_at IS NULL`。删除后同一路径重新出现会创建新 Source/Location，旧位置继续保留历史且不参与 live 匹配。path_key 是 NFC + `/` + Windows casefold 后的 SHA-256。relative_path 只存在于本地私有 SQLite，不进入错误和审计。

### 8.2 `ingestion_runs`

- `id`、`vault_id_hash`、`scan_scope_hash`、`manifest_hash`。
- `status`、`total_items`、`succeeded_items`、`skipped_items`、`quarantined_items`、`failed_items`。
- `error_category`、`revision`、`started_at`、`completed_at`、`created_at`、`updated_at`。

manifest_hash 基于按 path_key 排序的 `(path_key, content_hash, size)` 列表，不包含正文。

### 8.3 `ingestion_items`

- `id`、`run_id`、`source_id`（可空）。
- `action`、`relative_path`、`path_key`、`file_key`。
- `content_hash`（删除项可空）、`base_version_id`（可空）、`result_version_id`（可空）。
- `status`、`operation_id`、`attempt`、`error_category`、`safety_signals_json`。
- `revision`、`created_at`、`updated_at`、`completed_at`。

唯一约束：`(run_id, path_key, action)` 和 `operation_id`。`safety_signals_json` 是只含类别和非负计数的 canonical JSON；错误字段只能保存枚举化类别。

LangGraph `AsyncSqliteSaver` 使用独立 checkpoint 文件并自行管理 `checkpoints`、`writes` 等表（表名以锁定版本为准），不纳入 Alembic metadata。依赖固定 `langgraph-checkpoint-sqlite>=3.0.1,<4`，禁止调用方提供任意 metadata filter key。

迁移先创建 ingestion_runs，再创建 source_locations 和 ingestion_items，避免前向外键的建表顺序歧义；downgrade 采用相反顺序。另建 partial unique index，保证每个 vault_id_hash 只存在一个 PLANNING/APPLYING run。

## 9. Repository 扩展

### 9.1 `SourceRepository`

新增意图方法：

- `find_source_by_location(vault_id_hash, path_key)`
- `list_live_sources_for_vault(vault_id_hash)`
- `get_current_source_version(source_id)`
- `get_latest_source_version(source_id)`
- `find_source_version_by_hash(source_id, content_hash)`
- `get_source_version(version_id)`
- `move_source(source_id, canonical_uri, expected_revision)`

`move_source` 只能修改 canonical URI 和 revision；旧 SourceVersion.original_path 保持历史原值。

### 9.2 `IngestionRepository`

- `begin_run`
- `save_plan`
- `get_run`
- `list_pending_items`
- `start_item`
- `record_source_location`
- `move_source_location`
- `mark_source_location_deleted`
- `complete_item`
- `fail_item`
- `transition_run`
- `summarize_run`

所有状态更新使用 revision compare-and-swap，只 flush、不 commit。Unit of Work 新增 `uow.ingestion`。

## 10. 单项事务

### 10.1 CREATED

1. 在事务外稳定读取、写快照、解析和扫描。
2. 获取 `ingestion.file` 幂等 key；key 是 run ID、item ID 和 attempt 的规范哈希，同一 checkpoint task 重放保持不变，显式重试递增 attempt 后使用新 key。
3. 创建 Source 和 SourceLocation。
4. 创建 CAPTURED SourceVersion 与 ContentBlock。
5. 解析成功且无高风险信号：CAPTURED → PARSED → READY，并激活 Source current_version。
6. 创建 RECEIVED KnowledgeChange(CREATED)。
7. 追加不含正文/路径的 OperationLog 哈希链。
8. 完成 item 和幂等记录，显式 commit。

### 10.2 UPDATED

1. 读取 current 与 latest SourceVersion；current 存在时作为 base。
2. content_hash 首次出现时创建新 CAPTURED SourceVersion、结构块和哈希 Diff。若 latest 是相同哈希的 PARSE_FAILED，则复用其快照并重新解析，不创建重复版本。
3. 成功时转换 READY 并 CAS 切换 Source current_version。
4. 旧 current 存在时创建 KnowledgeChange(UPDATED)；此前从未激活时创建 KnowledgeChange(CREATED)。
5. 更新 SourceLocation 的观察值，完成审计、item 和幂等记录。

旧 ACTIVE/READY 版本在新版本解析失败、被隔离或事务失败时继续作为 current。

### 10.3 MOVED

内容未变时不创建重复 SourceVersion：

- CAS 更新 Source.canonical_uri。
- CAS 更新 SourceLocation path、path_key 和观察值。
- 创建 KnowledgeChange(MOVED)，base_version_id 和 target_version_id 都指向 current version。
- 记录旧/新 path_key，不记录原始路径。

### 10.4 DELETED

- CAS 逻辑删除 Source。
- 创建 KnowledgeChange(DELETED)，base/target 指向删除前 current；若从未激活则指向 latest 已捕获版本。
- CAS 设置 SourceLocation.deleted_at；记录保留用于审计，但不会参与 live path/move 匹配。
- 不删除 Vault 文件、快照或历史版本。

### 10.5 UNCHANGED

正常 READY 内容只更新 SourceLocation.last_seen_run_id 和观察元数据，item 标记 SKIPPED；不创建 SourceVersion、KnowledgeChange 或内容审计步骤。相同 QUARANTINED 内容不自动解除隔离，item 保持 QUARANTINED 并复用原安全类别。

### 10.6 解析失败与隔离

- 稳定字节已获得时仍保存快照和 CAPTURED SourceVersion。
- 结构解析失败：SourceVersion → PARSE_FAILED，item FAILED，不切 current。
- 高风险安全信号：SourceVersion → QUARANTINED，item QUARANTINED，不切 current。
- 不稳定读取、超限和路径违规发生在版本创建前，只记录 item 安全错误类别。
- 首次 CREATED 失败后 Source/Location 仍存在；后续同路径扫描走 UPDATED。相同 PARSE_FAILED 哈希可按新的 item attempt 重试既有版本，利用 L1 的 PARSE_FAILED → PARSED → READY 转换恢复；QUARANTINED 只有内容变化后才能产生新版本，L2 不提供人工解除隔离。

## 11. Diff

L2 只生成结构 Diff，不保存正文：

~~~json
{
  "added": [{"anchor": "...", "text_hash": "..."}],
  "removed": [{"anchor": "...", "text_hash": "..."}],
  "modified": [{"anchor": "...", "before_hash": "...", "after_hash": "..."}],
  "moved": [{"text_hash": "...", "from_anchor": "...", "to_anchor": "..."}],
  "counts": {"added": 0, "removed": 0, "modified": 0, "moved": 0}
}
~~~

数组按 anchor/hash 排序，canonical JSON 后计算 diff_hash。MOVED 文件路径 Diff 只含 path_key 哈希。

## 12. 恢复与一致性

### 12.1 正常重跑

重复扫描生成相同 manifest 和 plan。checkpoint 内重放依靠稳定 item/attempt 幂等 key；跨 run 重扫依靠 live location、`(source_id, content_hash)`、planner 与变更约束避免重复副作用。失败幂等记录保持终态，显式 item 重试必须先递增 attempt。

### 12.2 checkpoint 恢复

- LangGraph `thread_id = str(run_id)`。
- saver 在每个 super-step 和 pending task 写入后持久化。
- task 返回前必须完成对应 Unit of Work commit。
- checkpoint 写失败但业务已提交时，重放 task 命中幂等成功结果。
- 业务事务失败时 task 不返回成功，checkpoint 不前进。

### 12.3 无 checkpoint 恢复

checkpoint 文件损坏或丢失时，把旧 PLANNING/APPLYING run 标为 ABANDONED，开始新全量扫描。内容寻址快照和 L1 幂等状态仍保证安全重建。

### 12.4 同时运行

P0 同一 vault_id_hash 只允许一个非终态 run。唯一部分索引或事务内查询阻止第二个 run；返回 `IngestionAlreadyRunningError`。不自动等待或抢占。

## 13. 错误模型

公共错误：

- `IngestionError`
- `IngestionConfigurationError`
- `ObsidianCliUnavailableError`
- `ObsidianCommandError`
- `VaultPathPolicyError`
- `UnstableFileError`
- `DocumentTooLargeError`
- `UnsupportedEncodingError`
- `MarkdownParseError`
- `SnapshotIntegrityError`
- `IngestionAlreadyRunningError`

公共消息只能包含对象类型、脱敏 ID、path_key 前后缀和稳定类别。禁止包含绝对路径、relative_path、正文、Frontmatter、CLI stdout/stderr、SQL、URL 参数和凭据。

## 14. 测试策略

### 14.1 Unit

- 路径规范化、casefold、根目录包含和符号链接逃逸。
- raw/normalized/block/manifest/idempotency/diff 哈希。
- 快照首次写入、复用、并发写入和哈希冲突。
- Markdown frontmatter、标题、重复标题、段落、列表、表格、代码围栏、Unicode 和损坏输入。
- 安全信号只返回类别，不回显命中内容。
- Planner 的五种动作、歧义 move 和 move+modify 退化。
- 不完整 inventory 禁止删除、范围缩小不删除范围外 Source、读取失败不误删。
- 首次解析失败后同哈希重试、无 current 更新、隔离内容不自动解除。
- 全部新状态机合法/非法转换。

### 14.2 Repository 与 migration

- 三张新表、约束、索引、外键和 upgrade/downgrade/re-upgrade。
- Run/item 成功、失败、未找到、重复和 CAS 冲突。
- 同 Vault 只能一个活动 run。
- Source 删除后同路径重建不违反 live location 唯一约束。
- Source move 与 SourceLocation move 原子提交。
- Repository 不 commit；Unit of Work 未 commit 回滚。

### 14.3 合成 Vault 端到端

顺序场景：

1. 两个新 Markdown → CREATED。
2. 原样重扫 → UNCHANGED，无新版本。
3. 修改一个文件 → UPDATED，新版本激活。
4. 纯移动另一个文件 → MOVED，无重复版本。
5. 删除一个文件 → DELETED，逻辑删除。
6. 同一 manifest 重放 → 无重复副作用。

失败场景：

- 读取期间变化三次。
- UTF-8 损坏、超大文件、损坏 frontmatter、未闭合代码围栏。
- 快照写入成功后数据库 flush/commit 失败。
- item 提交成功后 checkpoint 写失败。
- 多个相同哈希文件导致 move 歧义。
- SQLite busy 和 checkpoint 文件暂时不可写。

所有 fixture 都是合成内容；禁止使用真实用户名、绝对个人路径、真实 Vault 结构、Token 或私人文本。

### 14.4 可选 CLI smoke

默认跳过。只有显式配置专用合成 Vault 且设置运行开关时，才调用：version、files、file、read。测试不得打印 read 内容。

## 15. CI 与质量门

- `uv lock --check`
- 公开仓库隐私扫描。
- Ruff check 与 format check。
- 严格 mypy。
- 非 integration pytest，覆盖率不低于 80%。
- Alembic 在线与离线迁移。
- checkpoint saver setup/resume 合成测试。
- wheel/sdist build。

真实 Obsidian CLI、真实 Vault 和 sub2api 均不属于 L2 默认 CI。

## 16. 实施顺序

1. L2 设计分支和依赖锁定。
2. IngestionSettings、错误、ID、枚举和冻结契约。
3. hashing、路径策略、StableMarkdownReader 和 SnapshotStore。
4. Markdown parser、结构 Diff 和安全信号。
5. Schema、ORM、第二个 Alembic revision 和迁移测试。
6. SourceRepository 扩展、IngestionRepository 和 Unit of Work 接入。
7. Planner 与单项 IngestionService。
8. 手动全量扫描 runner 和合成 Vault 闭环。
9. LangGraph workflow、独立 AsyncSqliteSaver 和恢复测试。
10. README、CLI smoke 契约、隐私扫描、完整质量门和 draft PR。

## 17. 验收条件

L2 只有在以下条件全部满足时完成：

1. 手动全量扫描只处理允许范围内的 Markdown。
2. 每个稳定输入都有可验证的 Vault 外不可变快照。
3. CREATED/UPDATED/MOVED/DELETED/UNCHANGED 均有确定性测试。
4. 更新失败或隔离时旧 current SourceVersion 不变。
5. 重复扫描和 checkpoint task 重放不创建重复版本、变更或审计副作用。
6. 中断后能从 checkpoint 恢复；checkpoint 丢失时能通过新全量扫描安全重建。
7. SQLite 和审计不保存 Markdown 正文，公共错误不泄露路径或 CLI 输出。
8. 不调用 LLM、网络、真实 Obsidian Vault 或真实个人数据。
9. ORM、Alembic head 和迁移产物一致。
10. 本地完整门禁与 GitHub CI 全部通过。

## 18. 依据

- [Obsidian CLI](https://obsidian.md/help/cli)：1.12.7+ 安装、显式 Vault 定位、files/file/read/move/history 等命令。
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：checkpoint、pending writes、thread_id、AsyncSqliteSaver 和故障恢复语义。
- [LangGraph SQLite Checkpointer GHSA-9rwj-6rc7-p77c](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-9rwj-6rc7-p77c)：`langgraph-checkpoint-sqlite` 3.0.1 之前 metadata filter key SQL 注入问题。
- [L1 领域与 SQLite 控制面设计](2026-07-28-l1-domain-sqlite-control-plane-design.md)：Repository、Unit of Work、幂等、审计、Schema 和安全错误边界。
