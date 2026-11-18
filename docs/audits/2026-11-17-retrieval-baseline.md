# 2026-11-17 检索基线与设计建议（阶段报告）

## 状态与范围

**尚未完成全目录审查，尚未实施检索优化，尚未完成真实 ONNX 语义回测。**

已盘点并冻结当前工作区 113 个非忽略文件（105 个 tracked、8 个原有 untracked）。保留原有 8 个已修改文件及全部未跟踪内容。模型、真实 Memory Graph store、人工独立 holdout 不在本次项目快照中；没有修改真实记忆、审批策略或在线索引。

两路只读审查均遭遇 `acp-proxy failure: upstream-timeout ETIMEDOUT chatgpt.com`。生命周期分支以原 `openai-codex/gpt-6.1-sol/high`、原生 retained-run 协议单路恢复一次，仍失败，累计两次后停止；设计分支未重试。三次失败均无有效审查报告。全目录覆盖不能按完成计算。

本报告证据均由主会话直接读取代码、执行隔离测试取得。回测是 **agent 编写、运行前冻结的合成诊断集，不是独立人工 holdout**。后续不得把修复此集后的提升当成泛化证明。

## 验证结果

- Python 3.14.6：250 tests，117.825 秒，退出码 0。
- Python 3.10.1：相同快照 250 tests，84.385 秒，退出码 0。
- Ruff 0.17.5：通过，退出码 0。
- 隔离快照构建：`chmemx-0.5.5.tar.gz`、`chmemx-0.5.5-py3-none-any.whl` 成功。
- 非 editable wheel、仓库外 MCP/CLI 生命周期烟测：`INSTALLED_WHEEL_MCP_LIFECYCLE_PASS`。
- 未验证其他操作系统；不能替代 CI 平台矩阵。

## 冻结检索回测

16 条合成 Active：12 条 global、Atlas/Borealis 各 2 条 project；另有一个未批准 Pending。43 个查询，v3 lexical backend，dense disabled，limit=3、association_limit=0。所有源内容、标签、查询先于查询执行写入冻结文件；运行期间未调参。

- 精确 Key：16/16 命中。
- 关键词查询：16/16 命中。
- 自然改写：0/2 命中。
- 跨语言：0/2 命中。
- 应无答案：2/4 正确拒答。
- 已知项目隔离：2/2 通过。
- Pending 排除：1/1 通过。
- 另测图关联：`workflow.git.authorization` 找到 `workflow.approval.exact`。
- 另测过期来源：修改 Atlas 源文件后，`atlas.database.port` 不进 entries，进入 needs_review。

这里只证明该冻结合成集的实际行为，不推断真实语义模型的准确率，也不把样本少的分类结果外推到生产。

Fixture SHA256：`cc45de3689f6ed24ce0a9badab2137867a8ede2af3d0ea3b33fb5979c3d6519b`。

索引 SHA256：`6c4d79cb5ba0a8ade32ec3bbdeb240a341d88c9256bca04499ca158e8938c084`。

## 已确认问题及机制

### P0：哈希碰撞可成为“可靠词法命中”

`scripts/vector_memory.py:126-165` 的 `text_tokens`、`token_bucket`、`sparse_vector` 实现非负哈希词向量；`scripts/retrieval_v3.py:167` 固定使用 2048 维。

查询「缓存中的登录会话能保留多久？」错误命中 `atlas.preference.modules`。两边 **没有相同 token**，但：

- `会话` 与 `atlas` 同落 bucket 37。
- `缓存` 与 `compact::modules` 同落 bucket 1193。
- sparse score=0.2107027，高于 0.17。

查询 `Who owns the invoice processing service?` 错误命中 `service.redis.expiry`：

- 两边没有相同 token。
- `processing::service?` 与 `redis` 同落 bucket 1736。
- sparse score=0.27140611，高于 0.17。

`Retriever.recall` 根据 sparse 分数建立 trusted 集；trusted 非空时，最终只从 trusted 排序，semantic 集不参与主结果。代码因此允许碰撞分数压住语义通道；**语义模型在这些查询上的真实输出尚未测量**。

unknown cwd 的跨项目词法 fallback 还会放大误召回。返回有 cross_project_reference 标记，符合当前设计，不应误报为“绕过权限”；但它是范围相关性风险，需要明确产品预期。

建议：保留哈希向量作为便宜的候选信号，不把它独自作为可信命中凭据。为精确 Key、真实词项/短语重叠、纯语义证据分别制定门槛；覆盖正文和批准的导航词，不能草率以当前 body-only BM25>0 替代全部导航能力。扩大哈希维度只能降低碰撞概率，不能修复判定模型。

### P0：正式评测无法表达应无结果

`scripts/vector_memory.py:709` 的 `load_evaluation_suite` 与 `schemas/recall-evaluation-v1.schema.json` 强制 expected_keys 非空。

实测空期望报错：`recall evaluation case none needs expected_keys`。因此本次拒答测试只能在独立诊断驱动中执行，现有正式质量门禁无法直接覆盖。

另一个契约不一致：suite 接受 top_k=64，v3 recall 只接受 1..20；实测 `LIMIT_INVALID`。

建议：正式引入显式 abstention 场景，分别报告命中、错误召回、拒答、source-blocked、scope/association violation；不把来源阻断当命中，不改旧标签。统一 evaluator/backend rank limits，并测试边界。

### P1：运行时重复验证和索引解析有可测成本

256 条长正文合成记录的索引为 1,791,315 bytes。已缓存 reader 的 Service.recall 仍重新读取、解析整个索引。

一次 global exact recall 启动 4 个 Git 进程：Service._reader 的 HEAD 检查、Retriever 前后两次 verify、legacy rank_pointers 的 ensure_fresh。5 次 profile 共 0.291 秒，其中 subprocess.run 累计 0.185 秒；这是该微基准的主成本，不证明所有真实场景都如此。

仅在内存中屏蔽被 v3 前后校验包围的 legacy HEAD 检查，交替顺序运行 30 对查询：

- 原始：p50 50.668 ms，p95 52.372 ms。
- 消融：p50 43.267 ms，p95 45.583 ms，Git 进程 4 降至 3。
- 所有调用的 record ID、完整 body、vector_pointer 相同；v3 前后 verify 未屏蔽。

**这是定位实验，不是已提交优化或完整安全证明。** 建议拆分纯排序与受检外层，减少重复工作；保留脏库、HEAD、Active 索引、选中正文、来源新鲜度和模型版本检查。索引缓存、dense matrix 缓存须独立验证换代、篡改与并发行为，不能靠关闭校验提速。

## 规模微基准

每档使用同一公开合成 fixture，长正文、精确 Key、limit=1、association_limit=0，20 次 warm recall，nearest-rank p95：

- 16 条：索引 120,700 bytes；rebuild 0.443 s；p50 32.275 ms；p95 34.482 ms。
- 64 条：索引 458,586 bytes；rebuild 1.664 s；p50 36.153 ms；p95 38.643 ms。
- 256 条：索引 1,791,315 bytes；rebuild 8.913 s；p50 52.728 ms；p95 59.169 ms。

无 ONNX 推理、无生产规模数据；不能据此宣布生产延迟达标。现有证据不支持立即引入向量数据库、扩大模型或重写图谱。

## 建议实施顺序（待共同确认）

1. 先补评测契约：拒答、近主题但无答案、哈希碰撞、来源过期、冲突、项目范围、图关联；保留冻结独立 holdout。
2. 修可信词法判定及与语义回退的关系；固定 scope/approval/source 安全边界。比较 exact/lexical/semantic 路由，避免一个碰撞命中封死后续路线。
3. 再做不改排序的性能工作：去冗余 HEAD 检查、预计算静态检索结构、审慎缓存索引/矩阵。每项同集前后对比。
4. 补齐生命周期及全目录审查后，再决定记忆组织、去重、淘汰、来源有效期策略；不在证据不足时批量清理真实记忆。

可保留的总体方向：Git 可审计权威记录，索引可重建；记忆是数据而非指令；读取检索不能代替写入批准。当前主要缺口是 **相关性信任与评测契约**，不是证明了整套架构需要推翻。

## 产物与未完成项

同目录 `2026-11-17-retrieval-baseline.json` 保存逐查询结果、碰撞 token、规模/消融结果、源文件 SHA256 和读取覆盖状态。

完整本地实验目录：`/tmp/chmemx-audit-20261117-iOO3MD/`，含 `probe.py`、`ablation.py`、冻结 fixture、隔离 store、profile、测试日志与构建快照。临时目录不是永久 CI 资产。

待确认：允许当前会话接手失败子任务的剩余审查；确认上述优化方向；提供可读取的本地 `model-lock.json`/ONNX 模型目录与冻结评测集（真实 store 如需读取，也需明确路径与范围）。不隐式下载模型，不借用其他 Agent 私有目录。
