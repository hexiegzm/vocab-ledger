# vocab-ledger · Copilot 指令

本仓库是一本**英语生词账本**。完整约定见 [AGENTS.md](../AGENTS.md)，数据格式见 [SPEC.md](../SPEC.md)。

## 做英语批改相关的事时

- 批改完成后，把**用户不会的词**写进账本：`python vocab.py add ... --note "<为什么记它>"`
- 用户说背完了才标记：`python vocab.py learn "<词>"`（别名 `kill`）；说又忘了：`vocab.py unlearn "<词>"`
- 用户说收藏才：`python vocab.py star "<词>"`（收藏与斩独立，斩掉的词收藏后仍在收藏夹）
- 错因必须具体：`写成 australian`、`介词用 for，应为 contributions to`；不要写「有错误」
- **不要**替用户判定「他会了」，只有他明确说了才 `learn`
- **不要**替用户生成周报，调度在系统层

## 代码约定

- 纯标准库，Python 3.10+，不引入第三方依赖
- 改格式或状态流转要同步改 `SPEC.md`，并补测试
- 测试：`python -m unittest discover -s tests`
