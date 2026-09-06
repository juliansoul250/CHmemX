# 队列维护

[English](../maintenance.md) · [v0.5](v0.5.md)

维护由操作者明确执行，不是 MCP 工具。它不清理正式 Active、审批历史、项目文件或外部 inbox，也不会在启动时或后台自动运行。

## 选择操作

| 操作 | 处理范围 | 默认时间 |
|---|---|---|
| `archive` | 已完成上传、被替换的审阅版本、已完成事件 | 30 天前 |
| `purge` | 压缩队列历史，保留最小请求回执 | 180 天前 |
| `reconcile` | 工作队列文件与元数据不一致 | 不按年龄筛选 |
| `expire-receipts` | 无历史正文、非工作队列的终态请求回执 | 至少 180 天 |
| `nonces` | 超过签名有效期及五分钟余量的防重放记录 | 按签名到期时间 |

归档和历史清理可以明确指定其他天数，包括 0；回执过期不能低于 180 天。每类每批默认 100 项，`--limit` 可设 1-500，剩余内容另生成新计划。没有明确时间、仍未解决或不属于托管队列的数据保留。

归档后，已批准或明确关闭的上传不再占工作队列名额，压缩历史及幂等回执仍可查询。这不是复制整个记忆 Git 仓库。

## 先预览，再执行

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id main-memory-curator maintenance-plan --action archive \
  --older-than-days 30 --limit 100 --output /absolute/archive-plan.json
```

检查 `targets`、`target_files`、`blockers` 和 `preserve`。计划列出逐文件哈希、前后大小、预计净释放空间和临时恢复日志所需空间。空间变化为负，表示会增加元数据。统计覆盖上传、候选、批次、归档及回执。计划 24 小时内有效。

明确确认这份计划后，再执行：

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id main-memory-curator maintenance-apply \
  --plan /absolute/archive-plan.json --digest 'sha256:EXACT_REVIEWED_DIGEST'
```

占位文本不是授权。文件、HEAD 或计划变化后必须重新预览；同一已完成计划不会执行两遍。`purge` 会删除列出的压缩正文，除非另有备份，否则不可恢复。`expire-receipts` 还会遗忘列出的请求编号绑定，之后这些编号可能被当成新请求。正式记忆的 Git 历史不受影响。

## 关闭不再需要的上传

不能为了腾空间直接丢弃 Pending 或冲突内容。先审阅，或明确取消/拒绝并注明原因：

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id main-memory-curator status --upload-id UPLOAD_ID
chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id main-memory-curator close-upload UPLOAD_ID --decision cancel \
  --reason 'No longer needed.' --digest 'sha256:EXACT_UPLOAD_DIGEST'
```

使用 `status` 返回的 `job_digest`；`reject` 表示拒绝。关闭会使当前审阅失效，不会修改 Active。正式记忆仍走 Owner 确认的订正流程，变化后的上传不能用旧摘要关闭。

## 对账与恢复

`maintenance-plan --action reconcile` 可以登记仍存在的上传文件，或从 Git 找回缺失上传的已提交结果。若正文和提交证据都不存在，就记录 `UPLOAD_DATA_MISSING`，保留请求指纹并释放孤立占位，不编造内容，也不静默重新接收同一请求。可以恢复原输入，或由操作者明确关闭这条无法恢复的任务。

不要手改 `state.json` 或直接删除上传/回执来清理。无法核实来源的旧请求，需要恢复原输入或使用新请求编号。

中断时，`status` 的 `queue_health` 会给出事务编号。队列写入暂停，正式记忆查询仍可用，备份需等事务恢复后进行：

```bash
chmemx --store /absolute/private-memory --agent-id main-memory-curator \
  maintenance-recover --transaction-id TRANSACTION_ID --action rollback
```

准备完整、字节和 HEAD 未变时，可以用 `complete` 继续。若准备阶段尚未写齐日志，应回滚再生成计划。目标或日志遭到额外修改时，恢复会停止，不覆盖它；不要删除恢复目录。
若数据处理已完成，只剩日志清理中断，应使用 `complete`。该状态不能再用中断恢复命令回滚；已清理的正文仍需独立备份才能找回。

日志处理进程中断，不代替硬盘损坏时的独立备份。升级不会自动开启清理或备份。

仅清理 nonce 时，先用 `maintenance-plan --action nonces`，再把对应摘要交给 `maintenance-nonces`，不能混用归档计划的摘要。
