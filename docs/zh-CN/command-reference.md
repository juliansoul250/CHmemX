# 命令参考

[文档目录](README.md)

以下命令均以这两个环境变量为例：

```bash
export MEMORY_GRAPH_KIT="/path/to/CHmemX"
export MEMORY_GRAPH_HOME="$HOME/.memory-graph/store"
```

`simple_memory.py` 还接受全局参数：

```text
--store <path>   覆盖 MEMORY_GRAPH_HOME
--cwd <path>     指定当前任务或项目目录
```

## 初始化与项目注册

创建新的真实记忆仓库，并注册第一个 Git 项目：

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" init \
  --project-root /path/to/git/project \
  --project-id project-example \
  --title "Example Project" \
  --confirmed
```

为现有记忆仓库增加项目：

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" register-project \
  --project-root /path/to/another/git/project \
  --project-id project-another \
  --title "Another Project" \
  --confirmed
```

项目根目录必须等于 `git rev-parse --show-toplevel` 返回的路径。

## 状态与读取

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" status

python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" \
  --cwd "$PWD" start --role main --query '任务关键词'

python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" \
  --cwd "$PWD" recall --query '查询主题'
```

子 Agent 使用 `start --role subagent`。

## Candidate 与批次

普通来源 Agent 不应直接运行以下命令。它们属于集中策展流程。

提交单个候选：

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" \
  --cwd "$PWD" propose \
  --candidate /path/to/candidate.json \
  --agent-id main-memory-curator
```

把一个或多个 Candidate 组成批次：

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" batch-create \
  --candidate-id <candidate-id-a> \
  --candidate-id <candidate-id-b>
```

审阅完整批次：

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" batch-review \
  --batch-id <batch-id>
```

从集中策展 Inventory 原子导入 Pending：

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" \
  --cwd "$PWD" import-pending \
  --inventory /path/to/curated.inventory.json \
  --agent-id main-memory-curator \
  --confirmed
```

## Approve

取得 Owner 精确确认后，策展者执行：

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" approve \
  --batch-id "$BATCH_ID" \
  --expected-digest "$DIGEST" \
  --confirmation-text "确认记忆批次 $BATCH_ID $DIGEST" \
  --agent-id main-memory-curator
```

提交者必须与最终策展批次的提交 Agent 一致。父 Git HEAD、Digest 或候选内容变化都会阻止写入。

## 上传包校验与集中整理

校验一个来源上传包，并生成 Pending Inventory 与报告：

```bash
python3 "$MEMORY_GRAPH_KIT/scripts/assemble_inventory.py" \
  --export /path/to/export.json \
  --output /path/to/pending.inventory.json \
  --report /path/to/validation.report.json
```

合并多个来源上传包，并与当前 Active Memory 对比：

```bash
python3 "$MEMORY_GRAPH_KIT/scripts/curate_uploads.py" \
  --export /path/to/agent-a.json \
  --export /path/to/agent-b.json \
  --curator-agent-id main-memory-curator \
  --curation-id curation-example \
  --output /path/to/curated.inventory.json \
  --report /path/to/curation.report.json
```

`curate_uploads.py` 默认从 `MEMORY_GRAPH_HOME` 读取当前 Active Memory。它只生成报告和 Inventory，不修改真实记忆仓库。

## 向量索引

构建索引：

```bash
python3 "$MEMORY_GRAPH_KIT/scripts/vector_memory.py" build \
  --store "$MEMORY_GRAPH_HOME" \
  --taxonomy /path/to/content-directory.json \
  --output /path/to/vector-index.json
```

覆盖已有索引时增加 `--replace`。

召回：

```bash
python3 "$MEMORY_GRAPH_KIT/scripts/vector_memory.py" recall \
  --index /path/to/vector-index.json \
  --cwd "$PWD" \
  --query '自然语言主题' \
  --limit 8
```

为上传包提供只读路由建议：

```bash
python3 "$MEMORY_GRAPH_KIT/scripts/vector_memory.py" route-upload \
  --index /path/to/vector-index.json \
  --export /path/to/export.json
```

## 回滚与备份命令

回滚使用新 Git Revert 提交，不删除原历史：

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" revert \
  --commit <full-40-character-commit> \
  --confirmation-text "确认回滚记忆提交 <full-40-character-commit>"
```

`backup` 和 `verify-backup` 属于可选快照策略：

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" backup \
  --backup-root /path/to/backup/root

python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" verify-backup \
  --directory /path/to/backup/directory
```

新仓库默认禁用 Snapshot。未由 Owner 明确配置并启用时，`backup` 会拒绝执行。不要把本地 Git 历史误当成硬盘损坏保护。
