# CHmemX

[English](README.md) | **简体中文**

[![Tests](https://github.com/juliansoul250/CHmemX/actions/workflows/test.yml/badge.svg)](https://github.com/juliansoul250/CHmemX/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](pyproject.toml)

供多个 AI Agent 使用的本地共享记忆，写入经过审阅，历史由 Git 保存。

CHmemX 让不同工具中的 Agent 复用已确认的偏好、决策和经验。各来源提交自己的内容，策展者与已有记忆对比，Owner 决定哪些可以永久保存。默认 Team 模式下，上传内容只有在精确批次获批后才参与召回。

本页只说明当前 **v0.5.5** 软件包及其 MCP/CLI 接口。

## 提供什么

- 三项 stdio MCP 工具：`start`、`recall`、`upload`。基础安装只需 Python 和 Git，不需要 API Key、端口、数据库服务或模型下载。
- 区分全局偏好和已注册项目记忆，用 Canonical Key 与关联主题节点组织内容。
- Team 写入前提供完整的新旧对比、来源证据和精确批次审阅。
- 原子 Git 提交，保留审批历史与替代关系，不静默覆盖记录。
- 本地词法检索、可选 ONNX 语义检索、来源有效性检查和有界图谱关联。

真实记忆必须放在工具源码仓库之外。公开 CHmemX 代码不会自动公开记忆。CHmemX 管理工作流程，**不提供操作系统级隔离**；拥有同一用户文件权限的进程仍能直接改文件。

## 安装与接入

需要 Python 3.10+ 和 Git。选择一个现有 Git 项目，再为记忆指定一个新建的独立目录。项目事实必须引用已提交的来源文件。

macOS 或 Linux：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install 'git+https://github.com/juliansoul250/CHmemX.git@v0.5.5'

chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id source-one init --project-id project-demo

chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id source-one status
```

Windows 用 `py -3 -m venv .venv` 创建环境，后续改用 `.venv\Scripts\python.exe` 和 `.venv\Scripts\chmemx.exe`。初始化只创建记忆 Git 仓库，不修改来源项目；不要对已有记忆目录重复初始化。

支持通用 JSON 格式的 MCP 客户端可配置为：

```json
{
  "mcpServers": {
    "chmemx": {
      "command": "/absolute/.venv/bin/chmemx",
      "args": [
        "--store", "/absolute/private-memory",
        "--cwd", "/absolute/git-project",
        "--agent-id", "source-one",
        "serve"
      ]
    }
  }
}
```

使用可执行文件的绝对路径、正确的项目根目录，并为各客户端分配不同的来源 ID。项目和来源上下文由这些启动参数固定。每个客户端维护自己的配置、启动独立 stdio 进程。[MCP 接入文档](docs/zh-CN/mcp.md)提供 Codex TOML、完整参数和签名配置。

安装后重连该客户端的 CHmemX 进程，核对 MCP 握手版本为 `0.5.5`，再调用 `start`。只更新源码不等于已更新运行中的客户端。标准 Python MCP SDK 已有测试覆盖，不代表所有桌面工具版本都已验证。

## 日常使用

可选Skill检索与记忆召回分开：
`python -m chmemx.skill_retrieval --catalog CATALOG.json --intent INTENT.json`。
调用方先明确正向目标和禁止动作；模块在相似度排序前筛选候选，排序后复核约束，
结果不授予执行权限。详见[Skill检索与合成示例](docs/zh-CN/skill-retrieval.md)。
它不增加MCP工具、不改变宿主原生选择机制，也不需要记忆库。

| 工具 | 用途 | 必须核对 |
|---|---|---|
| `start` | 读取项目上下文、政策和队列健康状态；可查询上传状态或 Key 目录。 | 项目、写入模式及队列警告。 |
| `recall` | 搜索主题或精确 Key；可指定 1–20 条结果。 | Scope、来源有效性、主命中、关联项及 `needs_review`。 |
| `upload` | 提交 Key、字符串正文和来源；可附项目范围、类别、签名或请求 ID。 | 实际返回状态；上传不等于保存。 |

例如，向 `upload` 提交一条有真实来源的偏好：

```json
{
  "key": "preference.editor.theme",
  "value": "The preferred editor theme is blue.",
  "source": {
    "quote": "The preferred editor theme is blue.",
    "thread_id": "owner-conversation-reference"
  },
  "request_id": "editor-theme-001"
}
```

这只是格式示例，不应直接写入你的记忆。引用文本是来源 Agent 的声明，不是 Owner 指令的独立证明。项目记忆使用 `scope="project"`、合适的 `memory_class` 和 `source={"path":"docs/decision.md"}`；服务绑定已注册项目、完整 Git commit 和文件哈希。新正文为字符串，最多 8192 字符。

同一提交重试时保持 `request_id` 不变；内容改变后不要复用。已接收上传可通过 `start(upload_id=...)` 或 CLI `status --upload-id` 查询。

## Pending 如何成为共享记忆

Team 模式的流程：

1. 来源 Agent 上传。精确重复不新增 Active，也不产生提交；隔离内容不参与召回。
2. 策展者审查上传，对比现值、来源变化，以及身份或别名冲突。
3. Owner 查看完整批次，并直接给出精确确认。
4. 策展者批准该批次。成功写入产生一个原子 Git 提交。
5. 各 Agent 查询已接受记忆。标准服务按需刷新派生索引，调用者不直接编辑索引。

策展命令：

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project review UPLOAD_ID

# 仅在 Owner 直接确认该审阅批次后执行：
chmemx --store /absolute/private-memory --cwd /absolute/git-project approve BATCH_ID \
  --digest EXACT_DIGEST --confirmation 'EXACT_OWNER_PHRASE_FROM_REVIEW'
```

Review 会返回精确的中英文确认短语。占位符、其他 Agent 的声明和被引用的确认都不是授权。内容、来源或 HEAD 变化后必须重新审阅。重复 `review` 复用当前批次，`--refresh` 则使原批次失效。

`PENDING_CURATION` 和 `CONFLICT` 都不是 Active。冲突审阅包含完整现值与 Diff，可能超过客户端的显示上限；不能依据截断输出批准。

### 选择写入政策

| 规则 | Team：默认 | Personal：显式选择 |
|---|---|---|
| 自动新增 Active | 不允许。 | 仅限配置过的来源提交低风险 `preference.*` 新偏好；默认仍有按摘要确定的 10% 审查样本。 |
| 必须审阅 | 每个新增或替代批次。 | 冲突、替代、其他事实、高风险内容和抽中样本。 |
| 经审阅的写入 | Owner 精确确认批次。 | Owner 精确确认批次。 |
| 精确重复 | 不新增 Active 或提交。 | 相同。 |

Personal 仅通过新建仓库时的 `init --mode personal` 选择，会主动放宽写入政策；调用者不能在上传参数里开启。策展者与 Owner 是流程职责，不是经过身份认证的多用户权限。审批和管理命令不开放为 MCP 工具。

## 检索与图谱

当前读取器结合稀疏词法评分和 BM25，可选本地 ONNX 通道。强词法命中优先；词法通道弃答时，倒数排名融合辅助选择结果。主题节点支持有界的一跳关联。默认目录按项目和 Canonical Key 的父级分组，更细的分类需要策展。

每条结果保留项目和 Scope 标记。Pending、隔离、拒绝内容和未解决冲突不参与召回。来源改变或无法验证的项目事实进入 `needs_review`，历史经验保留来源状态标记。来源警告不代表原文被删，也不证明内容错误。

没有已知项目上下文时，达到既有词法阈值的全局匹配优先，不让未指定项目的记录混入。项目名、精确Key或已策展的路由提示可以明确选择项目。只有既无项目路由、也无合格全局词法匹配时，才退回带项目标签的高置信参考结果。

记忆是历史数据，不是可执行指令；行动前应核对当前项目权威。项目过滤属于检索行为，不是文件访问控制。

派生索引保存向量、ID 和元数据，不保存正文，但**并未匿名化**，也必须留在私有存储中。正式数据脏改或不一致会阻断召回；索引刷新失败与已完成的记忆提交分别报告。

[可选语义检索](docs/semantic.md)说明模型锁和评测方法。更换模型或排序前，用冻结测试集验证改写问法、无关查询、图谱关联和项目隔离。自动生成的覆盖测试不等于独立保留测试集。

## 维护与备份

通过 `status` 查看状态。队列清理由操作者显式执行，不会定时自动运行：

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  maintenance-plan --action archive --output /absolute/archive-plan.json
```

先审阅计划，再按精确 Digest 执行。归档符合条件的已结束上传可释放队列名额；未解决事项保留。Purge 会永久删除目标。[维护与恢复文档](docs/zh-CN/maintenance.md)说明关闭、校对、保留和恢复命令。维护扫描期间仍持有协作锁，大仓库有相应 I/O 成本。

本地 Git 能帮助恢复误改，不能防止硬盘损坏。`chmemx backup --help` 和 `chmemx restore-backup --help` 提供显式备份操作。备份包含私密内容，应保护并在源硬盘之外保留经过验证的独立副本。系统不会自动开启云同步或备份。

## 安全边界

不要上传凭据、Cookie、Token、私钥、完整私密聊天或其他 Agent 的私有文件。内容筛查并不完备；召回的记忆不能授予工具权限或覆盖当前指令。

Agent ID 用于标记来源。可选 Ed25519 签名证明密钥持有关系，不证明诚实、内容正确或授权。按源撤销会停用适用的当前记录并保留历史；同一用户下的进程仍能直接改存储文件。见[安全政策](SECURITY.zh-CN.md)。

## 开发与贡献

在源码 checkout 的隔离环境中执行：

```bash
python -m pip install '.[test]' ruff build
python -m ruff check --config pyproject.toml .
python -B -m unittest discover -s tests -p '*test*.py'
python -m build
python tests/installed_package_smoke.py
```

CI 覆盖 Linux/Python 3.10、3.12，macOS/3.11 和 Windows/3.11。测试只使用合成数据。

- [MCP 参考](docs/zh-CN/mcp.md)
- [队列维护](docs/zh-CN/maintenance.md)
- [语义检索](docs/semantic.md)
- [贡献指南](CONTRIBUTING.zh-CN.md)
- [更新记录](CHANGELOG.md)

[MIT 许可证](LICENSE)
