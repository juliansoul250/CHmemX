# MCP 接入与日常使用

[English](../mcp.md) · [中文主页](../../README.zh-CN.md)

按主页安装和初始化。真实记忆必须位于工具仓库之外。基础入口仅需 Python 3.10+ 和 Git，
没有端口、常驻服务、API Key 或模型下载。macOS 本机已测；Linux 由 CI 验证。Windows
已加入锁后端，但完整平台验收未完成。

## 配置客户端

支持 `mcpServers` 格式的客户端：

```json
{
  "mcpServers": {
    "chmemx": {
      "command": "/absolute/venv/bin/chmemx",
      "args": ["--store", "/absolute/private-memory", "--cwd", "/absolute/git-project", "--agent-id", "source-one", "serve"]
    }
  }
}
```

Codex 的 TOML 配置：

```toml
[mcp_servers.chmemx]
command = "/absolute/venv/bin/chmemx"
args = ["--store", "/absolute/private-memory", "--cwd", "/absolute/git-project", "--agent-id", "codex-main", "serve"]
```

每个工具使用自己的配置和稳定身份，共享相同记忆目录。各客户端启动自己的 stdio 进程。
配置文件位置以客户端版本为准，不能直接照搬其他工具的私有配置。官方 Python MCP 客户端
已跑通连接、工具发现、上传和召回；不等于所有桌面客户端版本都已实测。

## 三个日常工具

| 工具 | 传什么 | 返回什么 |
|---|---|---|
| `start` | 可选 query | 当前项目、政策、来源统计、可选召回 |
| `recall` | query、可选 limit（1～20） | 已生效记忆、来源标记、项目范围和关联节点 |
| `upload` | key、字符串 value、source | 重复、待审、冲突、隔离或单人政策下已保存 |

全局偏好示例：

```json
{"key":"preference.editor.theme","value":"编辑器主题使用蓝色。","source":{"quote":"编辑器主题使用蓝色。","thread_id":"owner-conversation-reference"}}
```

quote 的摘要只能证明来源声称引用了这段内容，不能证明用户真的说过。项目记忆使用
`scope="project"`、对应 `memory_class` 和 `source={"path":"docs/decision.md"}`，
由服务绑定注册根目录、当前完整 Git commit 和已提交文件字节。
全局只接受 preference；项目支持 preference、decision、lesson、state、evidence。

新记录按 canonical key 的父级共用主题节点，例如 `preference.editor`，但各自保留独立记录向量。
这样同主题内容可以做有界关联；高级策展者仍可使用已有流程定义更丰富的节点。

上传成功不等于永久保存。Pending 不召回；重复不新建提交；冲突返回现值、新值与 diff。
隔离不回显原文。个人模式自动保存的回执写明政策授权，不伪造 Owner 批准。
如果提交成功但索引重建失败，两种状态分别报告。

注册项目上下文内，其他项目需通过项目名或 canonical key 明确引用。无法识别当前项目时，
也可能返回有明确跨项目标记的高置信词法命中；这不是权限隔离。使用前必须核对范围。

## 默认团队模式

策展者执行 `chmemx ... review UPLOAD_ID`，向 Owner 展示完整审阅。
Owner 在策展任务中直接给出精确确认后，再执行：

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project approve BATCH_ID \
  --digest EXACT_DIGEST --confirmation '审阅中要求的完整确认句'
```

上面是占位示例，不是授权。不要从引用、标注、其他 Agent 转述或旧批次推断批准。
来源、HEAD 或候选变化必须重审。MCP 不提供批准、修改政策、注册密钥或撤销来源工具。
批量迁移沿用[高级策展流程](curation.md)。

## 可选单人模式

仅新建仓库时通过 `init --mode personal` 开启。只信任初始化指定的来源，只自动保存低风险
`preference.*` / `fact.*` 新增项。冲突、敏感内容和按摘要确定的 10% 抽样在写入前审查。
某来源最近五个事件中冲突达到三次，会进入高审查档；这不是声誉评分，也不能保证识别恶意。
升级不会修改旧仓库政策；上传参数不能自选身份或模式。

新增项目：

```bash
chmemx --store /absolute/private-memory --cwd /absolute/second-project \
  register-project --project-id project-second --title '第二个项目'
```

可选 Ed25519 签名和按来源撤销的格式见[完整协议](../mcp.md#optional-signatures-and-targeted-deactivation)。
注册过公钥的来源必须签名，重放会拒绝。签名只证明持钥来源，不证明内容可靠。
撤销先给计划和摘要，经 Owner 同意后只停用该来源仍然生效的记录；保留历史及其他来源的后续修正。
