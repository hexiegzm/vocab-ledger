# vocab-ledger · Claude Code 入口

本仓库的完整约定在 **[AGENTS.md](AGENTS.md)**。请先读它，再动手。

@AGENTS.md

---

## 如果你的版本没有展开上面的引用，记住这四条

1. **批改完必须落库**，两件事都要做：
   ```bash
   python vocab.py add "<词>" --zh "<释义>" --tag "<主题>" --wrong --note "<具体错因>"
   python vocab.py mark "<词>" wrong --note "<具体错因>"
   ```
2. **错因要写到「具体错在哪」**：`写成 australian`、`介词用 for，应为 to`。不要写「错了」「不熟」。
3. **不要手改** `data/words.json` 里的 `score` / `status`——它们由事件推导。
4. **不要自己出周报**：调度在系统层，每周固定时间会跑。

数据格式见 [SPEC.md](SPEC.md)，能力边界见 [README.md](README.md#让-ai-帮你记)。
