# 架构说明

[文档目录](README.md) | [English](../architecture.md) | [交互式架构图](architecture.html)

CHmemX 把工具代码、待整理输入和永久记忆分开保存。三者用途不同，不能混用。

## 三个存储平面

### CHmemX 工具仓库

`MEMORY_GRAPH_KIT` 保存 Runtime、脚本、Schema、Skill、文档和测试。它可以公开发布，因为仓库内只应出现合成数据和脱敏示例。

更新这个仓库不会读取、复制或推送真实记忆。

### 待整理中转区

各来源 Agent 把 `memorygraph-agent-export-v1` 文件写入自己的 `inbox/<agent-id>/`。这些文件只是输入材料，状态为 `PENDING_CURATION`。

中转区不属于 Active Memory。读取流程不会从这里召回内容。

### 真实记忆 Git 仓库

`MEMORY_GRAPH_HOME` 是永久记忆的权威仓库，包含：

- 全局 Active Memory；
- 各注册项目的 Active Memory；
- Active Index 与节点图；
- 审批凭证、替代关系和 Git 历史；
- 未进入 Git 的本地 Pending 队列。

真实记忆仓库默认只在本机存在。是否配置私有远程或外部备份，由 Owner 单独决定。

## 写入链路

来源 Agent 只能上传。集中策展者读取共享上传包后，依次完成 Schema 与来源校验、秘密扫描、去重、Active 对比和内容路由。

没有冲突的内容可以进入最终候选。存在冲突时，策展者必须向 Owner 展示当前值、新值、双方来源、字段差异和正文 Diff。Owner 决定保留、替代、重写合并或拆成不同 Key。

策展者随后创建 Pending 批次并输出完整审阅结果。Owner 使用 Batch ID 与 Digest 精确确认后，Runtime 再检查父 Git HEAD、候选顺序、正文、来源和提交者身份。所有检查通过才会生成一个 Git 提交。

整批写入是原子的。某一项失败时，不能留下半批 Active Memory。

## 读取链路

读取分两层：

1. `simple_memory.py start` 根据当前 Git 项目根目录，查询全局 Scope 与对应项目 Scope。
2. `vector_memory.py recall` 用自然语言查询内容网格，匹配路由节点，沿关系扩展一跳，再加载关联的 Active 记录。

默认向量器使用 NFKC 规范化、英文单词、中文 2/3 字符片段和 SHA-256 特征哈希。它生成词法稀疏向量，不是神经网络语义嵌入。

派生索引保存稀疏向量、元数据和记录路径，不复制记忆正文。每次召回都会核对索引绑定的 Memory Graph Git HEAD。HEAD 不一致时，索引拒绝返回旧结果。

## 权威与范围

项目源码、正式文档和数据是项目事实的最高权威。记忆用于续接，不取代项目本身。

每条 Active Memory 都保留 `scope`、`project_id`、`class`、Canonical Key 和来源。跨项目结果会明确标记，不能因为关键词相近就混成同一事实。

一个 Canonical Identity 由 `(project, scope, class, canonical key)` 确定。同一 Identity 同时只允许一个当前 Active 值。

## 失败时的处理

- Upload、Candidate、Quarantine、Rejected 和未解决冲突不参与召回。
- 来源变化、父 Git HEAD 变化或批次 Digest 变化都会使旧确认失效。
- 向量索引过期时，先回退到普通范围查询；只有策展者可以重建正式索引。
- Git 工作区出现未经批准的已跟踪改动时，Runtime 拒绝永久写入。

交互式版本见[中文架构图](architecture.html)。图中每个核心节点都绑定到仓库源码位置和基线提交。
