# P0 生命周期恢复规格

日期：2026-07-28
状态：已实现

## 1. 目标

本规格补齐 P0 验收项中的三个恢复边界：知识删除与恢复、Embedding/Chunker 代际升级、已切换
代际回滚。控制真源仍是 SQLite；Vault 和 Milvus 都是必须经过强验证才能改变实时指针的外部
状态。永久删除和自动清理旧 Collection 不属于 P0。

## 2. 不变量

1. `deleted_at` 非空的 Note 永远不能通过当前版本解析器进入问答。
2. 删除必须先禁止召回，再移动 Vault 文件和删除向量。
3. 恢复必须先验证 Vault 与完整 Chunk 集，再清除 `deleted_at`。
4. 一个实时 Note 的 `active_index_generation_id` 必须等于全局 ACTIVE Generation。
5. STAGING Generation 不参与默认问答。
6. 新 Generation 只有在全部实时 Note 已重建、强读回一致且绑定 Golden 门通过后才能激活。
7. 代际切换在单个 SQLite 事务内同时更新 Generation、Note 和双方 IndexJob。
8. 旧 Collection 默认保留；回滚必须先逐 Chunk 验证旧 Collection 完整。
9. 生命周期 OperationLog 只保存 ID、状态、计数和哈希，不保存正文、问题或答案。

## 3. 删除 Saga

```text
ACTIVE note + ACTIVE_INDEXED job
  -> SQLite: deleted_at + DELETE_PENDING（立即停止召回）
  -> Vault: final -> _AI/Trash/<note_id>/<version_id>.md
  -> Milvus: 按稳定 Chunk ID 删除并强读回为空
  -> Answer snapshots: 删除引用失效 Chunk 的私有结果
  -> SQLite: DELETE_PENDING -> DELETED
```

Vault 或 Milvus 失败时 Note 保持软删除且 Job 保持 `DELETE_PENDING`。同一操作可安全重跑；Vault
移动、Chunk 删除和答案失效都具备幂等语义。

## 4. 恢复 Saga

```text
deleted note + DELETED job
  -> SQLite: DELETED -> PENDING -> INDEXING
  -> 从内容寻址 PublicationSnapshot 确定性重建 Chunk
  -> Milvus Upsert + 强读回完整集合
  -> SQLite: INDEXING -> INDEXED
  -> Vault: recycle -> final，并验证版本 ID 与内容哈希
  -> SQLite: deleted_at -> null；INDEXED -> ACTIVE_INDEXED
```

向量或文件恢复成功但最后事务失败时，当前版本解析器仍排除该 Note；重跑从已验证边界继续。
答案快照不会因恢复而复活，避免重放删除前可能包含敏感内容的答案。

## 5. 代际迁移

1. `generation create` 为当前模型、维度、Chunker 和 Schema 创建不可变 STAGING Generation。
2. 对旧 ACTIVE Generation 的每个实时 Note 读取当前 CuratedVersion 和内容寻址快照。
3. `generation rebuild` 在新 Collection 中重建完整 Chunk 集，每个版本使用独立 IndexJob 并强读回；
   此时旧 Generation 和全部实时指针保持不变。
4. 基于已重建代际生成 observations。门禁 JSON 必须由确定性评估入口生成并携带目标
   `generation_id`；最低要求为 Citation Precision
   95%、Retrieval Recall 90%、Refusal Accuracy 100%、Unsafe Citation 0。
5. `generation promote` 重新强读全部 Chunk 并验证门禁，然后在单事务把旧 Generation 标记
   `SUPERSEDED`、新 Generation 标记 `ACTIVE`，更新全部 Note 指针，同时把旧 Job 置为
   `DELETE_PENDING`、新 Job 置为 `ACTIVE_INDEXED`。

任一重建任务失败时，新 Generation 保持 STAGING，旧 ACTIVE Generation 和所有实时指针不变。
运维可修复后用同一 operation ID 继续，或执行 `generation abort` 标记 FAILED。

## 6. 回滚

回滚只接受 `SUPERSEDED` Generation。服务用该 Generation 的不可变配置重新计算预期 Chunk ID，
逐项强读旧 Collection 并核对 `chunk_id`、`curated_version_id` 和 `content_hash`。所有 Note 都通过后，
才在一个事务中反向切换 Generation、Note 与 IndexJob。旧 Collection 缺失或混合指针都会阻断，
不会尝试用当前模型伪造旧向量。

## 7. 自动化验证

- 状态机：`DELETED -> PENDING` 恢复和 `DELETE_PENDING -> ACTIVE_INDEXED` 回滚只允许显式路径。
- Vault：回收、重复回收、恢复、重复恢复和身份哈希验证。
- Answer snapshots：只删除引用失效 Chunk 的结果。
- SQLite：软删除立即排除当前版本，恢复后指针与 Job 同步。
- 端到端：临时 Vault、SQLite 和内存向量库完成删除/恢复、迁移/回滚及 operation ID 重放。
- 故障：新模型嵌入失败时旧代际保持 ACTIVE，失败代际可显式 abort。
