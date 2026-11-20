# vocab-ledger · Claude Code 入口

本仓库的完整约定在 **[AGENTS.md](AGENTS.md)**。请先读它，再动手。

@AGENTS.md

---

## 如果你的版本没有展开上面的引用，记住这四条

1. **批改完必须落库**：
   ```bash
   python vocab.py add "<词>" --zh "<释义>" --tag "<主题>" --note "<具体错因>"
   ```
2. **错因要写到「具体错在哪」**：`写成 australian`、`介词用 for，应为 to`。不要写「错了」「不熟」。
3. **用户说「会了/斩了」才 `learn`**（别名 `kill`），说「收藏」才 `star`。两者互不影响：斩掉后收藏的词仍在收藏夹。不要替他判定。
4. **不要自己出周报**：调度在系统层，每周固定时间会跑。

数据格式见 [SPEC.md](SPEC.md)，能力边界见 [README.md](README.md#让-ai-帮你记)。
