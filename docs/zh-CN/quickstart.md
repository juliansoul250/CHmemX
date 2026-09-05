# 快速开始

v0.3 日常接入请先看 [MCP 配置](mcp.md)。以下手动流程保留给已有仓库和批量策展者，
不再要求每个使用者逐项操作。

[文档目录](README.md) | [English](../quickstart.md)

## 1. 环境要求

- Python 3.10 或更高版本；
- Git；
- 一个用于首次注册的真实 Git 项目。

克隆工具仓库：

```bash
git clone https://github.com/juliansoul250/CHmemX.git
cd CHmemX
```

设置两个不同的路径：

```bash
export MEMORY_GRAPH_KIT="$PWD"
export MEMORY_GRAPH_HOME="$HOME/.memory-graph/store"
```

- `MEMORY_GRAPH_KIT` 指向 CHmemX 工具代码。
- `MEMORY_GRAPH_HOME` 指向你的真实本地记忆 Git 仓库。

不要把两者设为同一个目录。

## 2. 初始化一次

确认第一个项目是 Git 仓库根目录：

```bash
git -C /path/to/first/git/project rev-parse --show-toplevel
```

初始化：

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" init \
  --project-root /path/to/first/git/project \
  --project-id project-example \
  --title "Example Project" \
  --confirmed
```

该命令创建独立的 `MEMORY_GRAPH_HOME` Git 仓库，不修改来源项目。初始化属于本地写入操作，不应重复执行。

检查状态：

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" status
```

## 3. 构建首次向量索引

默认示例分类表只用于演示。正式使用时应复制并维护自己的内容分类文件：

```bash
cp "$MEMORY_GRAPH_KIT/examples/content-directory.example.json" \
  "$HOME/.memory-graph/content-directory.json"

python3 "$MEMORY_GRAPH_KIT/scripts/vector_memory.py" build \
  --store "$MEMORY_GRAPH_HOME" \
  --taxonomy "$HOME/.memory-graph/content-directory.json" \
  --output "$HOME/.memory-graph/vector-index.json"
```

## 4. 每个任务开始时读取

```bash
python3 "$MEMORY_GRAPH_KIT/runtime/simple_memory.py" \
  --cwd "$PWD" start --role main \
  --query '3 到 8 个非秘密关键词'

python3 "$MEMORY_GRAPH_KIT/scripts/vector_memory.py" recall \
  --index "$HOME/.memory-graph/vector-index.json" \
  --cwd "$PWD" \
  --query '当前任务的自然语言主题'
```

只读取 `accepted + active` 记录。索引绑定的 Memory Graph Git HEAD 一旦过期，向量召回会拒绝继续，必须由策展者在审批提交后重建。

首次索引完成后，后续每次 Active 提交都应改用质量门禁发布：

```bash
python3 "$MEMORY_GRAPH_KIT/scripts/vector_memory.py" optimize \
  --store "$MEMORY_GRAPH_HOME" \
  --taxonomy /path/to/content-directory.json \
  --suite /path/to/recall-evaluation.json \
  --output "$HOME/.memory-graph/vector-index.json" \
  --report "$HOME/.memory-graph/recall-quality-report.json" \
  --replace
```

优化器根据全部 Active 记忆重算 IDF 和记录向量，并比较受限评分参数。自动覆盖测试或黄金查询未达到阈值时，不会替换正式索引。黄金查询必须脱敏，不记录真实任务全文。

## 5. 来源 Agent 上传

来源 Agent 使用 [`agent-export-v1.schema.json`](../../schemas/agent-export-v1.schema.json) 和[虚构示例](../../examples/global-preferences.export.json)生成：

```text
inbox/<stable-agent-id>/<export-id>.json
```

来源 Agent 只能上传自己的长期内容。完成后报告：

- 绝对文件路径；
- 条目数；
- 被拒绝或隔离的条目数；
- 文件 SHA-256。

随后停止。上传包仍是 `PENDING_CURATION`，不能被召回，也不能自行写入真实记忆仓库。

## 6. 策展者整理与审批

只读检查路由建议：

```bash
python3 "$MEMORY_GRAPH_KIT/scripts/vector_memory.py" route-upload \
  --index "$HOME/.memory-graph/vector-index.json" \
  --export "inbox/<agent-id>/<export-id>.json"
```

整理上传包：

```bash
python3 "$MEMORY_GRAPH_KIT/scripts/curate_uploads.py" \
  --export "inbox/<agent-id>/<export-id>.json" \
  --curator-agent-id main-memory-curator \
  --curation-id curation-example \
  --output "outbox/curation-example.inventory.json" \
  --report "outbox/curation-example.report.json"
```

只有冲突全部解决、候选最终确定后，策展者才能导入 Pending 并展示完整 `batch-review`。Owner 使用工具打印的 Batch ID 与 Digest 精确确认；策展者随后执行 `approve`，再重建向量索引。

详见[集中策展与冲突处理](curation.md)和[命令参考](command-reference.md)。

## 7. 运行测试

```bash
python3 tests/test_assemble_inventory.py
python3 tests/test_curate_uploads.py
python3 tests/test_vector_memory.py
PYTHONPATH=runtime python3 tests/simple_memory_test.py
python3 tests/test_docs.py
```

五组测试全部通过，才能认为当前工具代码和文档在本机通过基础验收。
