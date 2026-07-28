# Trustworthy Personal Knowledge Assistant

一个以 Obsidian 为人工可读知识真源、以 SQLite 管理知识血缘和状态、以 Milvus 提供可重建混合检索的可信个人知识助手。

项目当前处于设计与工程基线阶段，尚不适合保存或处理真实私人资料。

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

