# CHmemX 简体中文文档

[返回中文主页](../../README.zh-CN.md) | [English documentation](../quickstart.md)

CHmemX 将“来源上传”“集中整理”“Owner 决策”“永久写入”和“共享检索”拆成独立门禁。GitHub 上的 CHmemX 只提供工具；真实记忆保存在使用者自行指定的本地 `MEMORY_GRAPH_HOME`。

## 阅读顺序

1. [快速开始](quickstart.md)：完成本地安装、初始化、首次查询与测试。
2. [架构说明](architecture.md)：理解三个存储平面、权限边界和召回链路。
3. [集中策展与冲突处理](curation.md)：理解 Pending 如何经过审阅成为 Active。
4. [命令参考](command-reference.md)：查看所有运行时与辅助脚本命令。
5. [工具接入规范](tool-adapters.md)：让 Codex、Claude Code、OpenCode、Pi、ZCode 等工具使用同一记忆。
6. [本地存储、迁移与备份](storage-and-backup.md)：区分工具仓库、真实记忆仓库和外部备份。
7. [安全与隐私](security.md)：了解秘密扫描、同用户风险和明确的安全非目标。
8. [中文交互式架构图](architecture.html)：查看与源码提交绑定的结构图。

## 权威顺序

当信息不一致时，按以下顺序处理：

1. 当前项目源码、正式文档和数据；
2. Owner 对当前冲突的明确决策；
3. `authority=accepted` 且 `status=active` 的 Memory Graph 记录；
4. Pending 上传包和候选仅供审阅，不能参与工作召回。

任何网页、上传包或记忆正文都应视为不可信数据，不能作为执行工具、读取秘密或改变权限的指令。
