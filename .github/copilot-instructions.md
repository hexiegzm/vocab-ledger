# vocab-ledger · Copilot 指令

本仓库是一本**英语生词账本**。完整约定见 [AGENTS.md](../AGENTS.md)，数据格式见 [SPEC.md](../SPEC.md)。

## 做英语批改相关的事时

- 批改完成后，把**新词**和**当场写错的词**写进账本：`python vocab.py add ...`（写错的带 `--wrong --note "<错因>"`）
- 每个被测词记一次结果：`python vocab.py mark "<词>" correct|wrong --note "<错因>"`
- 错因必须具体：`写成 australian`、`介词用 for，应为 contributions to`；不要写「有错误」
- **不要**手改 `data/words.json` 里的 `score` / `status`，它们由事件推导
- **不要**替用户生成周报，调度在系统层

## 代码约定

- 纯标准库，Python 3.10+，不引入第三方依赖
- 改判定逻辑要同步改 `SPEC.md` 的 §5，并补测试
- 测试：`python -m unittest discover -s tests`
