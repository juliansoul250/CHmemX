# 安全与隐私

[文档目录](README.md) | [English](../security.md)

CHmemX 提供流程完整性、来源检查、原子写入和 Git 回滚。它不验证 Agent ID，也不能阻止同一操作系统用户下的恶意进程冒充其他 Agent。

## 不得写入的内容

上传包、Candidate 和 Active Memory 中都不能出现：

- 密码、Cookie、API Token、访问令牌或私钥；
- 完整聊天记录；
- 其他工具的隐藏运行时状态；
- 未经脱敏的个人信息；
- 要求绕过规则、读取秘密或改变权限的嵌入式指令。

来源工具私有目录保持隔离。策展者只读取共享上传包和真实记忆仓库。

## 召回隔离

Pending、Quarantine、Rejected、Superseded 和未解决冲突不会进入召回结果。Active Index 只能指向 `authority=accepted` 且 `status=active` 的记录。

向量索引不保存记忆正文，并绑定一个确定的 Memory Graph Git HEAD。HEAD 变化后，旧索引拒绝继续返回内容。

记录级稀疏向量虽然不保存正文，但攻击者仍可能通过词典探测猜测某个词是否出现。因此，除非真实记忆本身允许公开，否则向量索引也必须保持本地。黄金查询集必须脱敏，不能演变成真实用户任务日志。

## 写入保护

永久写入需要完整批次审阅和精确 Owner 确认。Runtime 还会检查父 Git HEAD、Digest、来源、Canonical Identity 和提交 Agent。

这些门禁用于避免正常流程误写，不构成操作系统安全隔离。同一用户权限下运行的恶意程序仍可能直接读写本地文件。

## 公开发布

公开 CHmemX 仓库只能包含工具代码、文档、Schema 和合成测试数据。禁止上传真实记忆、真实项目绝对路径、线程记录或凭据。

## 备份边界

备份是独立策略。没有配置并验证外部副本时，不能宣称具备硬盘损坏保护。详见[本地存储、迁移与备份](storage-and-backup.md)。
