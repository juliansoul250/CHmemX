# 参与贡献

[English](CONTRIBUTING.md) | [中文主页](README.zh-CN.md)

提交改动时必须保持以下约束：

- 来源 Agent 只上传，不直接提升为永久记忆；
- 召回只返回 accepted Active 记录；
- 项目源码和正式文档高于记忆；
- 冲突必须由 Owner 明确决定；
- 永久写入是绑定精确批次审阅的原子 Git 提交；
- 向量索引不保存正文，并在 Git HEAD 过期时拒绝读取；
- 测试只能使用合成数据。

提交 Pull Request 前，运行 README 中列出的全部测试。
