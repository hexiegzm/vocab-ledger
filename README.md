# vocab-ledger · 让 AI 把「你不会什么」记下来

![tests](https://github.com/hexiegzm/vocab-ledger/actions/workflows/tests.yml/badge.svg)

> 每次英语作业批改完，AI 把生词和错因写进一个本地 JSON 文件；
> 每周日，它变成一张能打印的 A4。
>
> 不需要 Anki，不需要账号，不联网。

![周报预览](docs/preview.png)

---

## 为什么做这个

用 Anki 的人多半死在「录入」上，不是死在复习算法上。看见生词 → 建卡 → 填释义 → 加标签 → 选牌组，三次之后就懒得动了。

而 AI 批改作业的那一刻，**词、错因、上下文全都在场**，录入的边际成本几乎为零。

所以这里的取舍很明确：**把录入成本压到零，哪怕复习端做得糙一点。**

三条判断贯穿整个设计：

1. **录入发生在批改当场**，而且是 AI 做，不是人做。人只负责背。
2. **复习按周批量发生**，一张纸、一个晚上，不是每天弹通知。
3. **AI 判断，脚本算术**。AI 决定「这个词他没掌握」和「错在哪」，脚本负责计分、排序、渲染。这条边界让整个东西可以离线跑——AI 不在，数据还在。

## 30 秒上手

只需要 Python 3.10+，没有第三方依赖。

```bash
git clone https://github.com/hexiegzm/vocab-ledger.git
cd vocab-ledger

# 导入示例数据
python vocab.py add --file examples/words.sample.json

# 生成本周报告并打开
python vocab.py report --open
```

打开 `reports/vocab-2026-W47.html`，就是上面那张纸。

清空示例数据后，你就得到一本空账本：

```bash
rm -rf data reports logs
```

## 它怎么工作

```
你交作业
   │
   ▼
AI 批改 ──► add   ：不认识的词 + 音标 + 释义 + 搭配 + 为什么记它
   │
   ▼
data/words.json        唯一数据源：纯文本、可手改、可 diff、自动备份
   │
   ▼  每周日（系统调度，不是 AI）
vocab.py report ──► 单文件 HTML ──► 打印，贴桌上
   │                                     │
   └────────── 背完了，说一句「XX 会了」──┘
```

整个环里唯一的「智能」在第一环——判断哪些词值得记。后面全是死规则，所以它可以一直跑、可复现、换模型也不变形。

## 命令

```bash
python vocab.py add --file seed/week47.json      # 批量录入（幂等，重复导入不会重复计）
python vocab.py add resilient --zh 有韧性的 --tag 写作搭配 --note "写成 resistent"
python vocab.py learn resilient                  # 背完了，从清单里划掉
python vocab.py unlearn resilient                # 又忘了，放回去
python vocab.py list                             # 还没背的
python vocab.py list --learned                   # 背过的
python vocab.py list --week 2026-W47             # 某周新增的
python vocab.py stats                            # 总账
python vocab.py weeks                            # 按周统计
python vocab.py report --open                    # 出周报
python vocab.py report --week 2026-W46 --open     # 补出旧周报
python vocab.py weekly --open                     # 补漏 + 出当周（幂等，给调度用）
python vocab.py doctor --task VocabLedgerWeekly   # 自检：账本 / 报告 / 计划任务
```

## 让 AI 帮你记

这是这个仓库的重点。**它一半是工具，一半是协议。**

| 文件 | 作用 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | 仓库内的工作约定。`AGENTS.md` 是跨工具的事实标准，Codex、DSH、Cursor 等直接读——AI 进这个目录就自动知道该干什么 |
| [`CLAUDE.md`](CLAUDE.md) | 给 Claude Code 的入口（它默认读这个名字，不读 `AGENTS.md`），内容用 `@AGENTS.md` 引用，不重复维护 |
| [`.github/copilot-instructions.md`](.github/copilot-instructions.md) | 给 GitHub Copilot 的入口 |
| [`skills/vocab-ledger/SKILL.md`](skills/vocab-ledger/SKILL.md) | 同一套流程的 skill 版，让 AI 在**任何对话**里都能想起「该记账了」，不必先进这个目录 |
| [`prompts/universal.md`](prompts/universal.md) | 给**不能读写文件**的 AI（豆包、网页版 ChatGPT、手机 App）——一段提示词，让它输出符合 SPEC 的 JSON，你复制一次即可入库 |
| [`SPEC.md`](SPEC.md) | 协议本体：字段、判定公式、渲染契约。换语言实现对照这份 |

### 哪些 AI 能自动记

判断标准只有两条：**读不读项目里的约定文件**、**有没有终端能执行命令**。

| 工具 | 自动记录 | 说明 |
|---|---|---|
| Codex CLI / DSH / Cursor / Gemini CLI | ✅ | 读 `AGENTS.md`（个别版本用 `GEMINI.md`，指向它即可） |
| Claude Code | ✅ | 读 `CLAUDE.md`，本仓库已提供 |
| GitHub Copilot | ✅ | 读 `.github/copilot-instructions.md`，本仓库已提供 |
| 豆包 / 网页版 ChatGPT / Kimi | ⚠️ 半自动 | 没有本地文件与终端权限。用 [`prompts/universal.md`](prompts/universal.md)，它批改输出 JSON，你贴一次 |
| 手机 App | ❌ | 同上，且没有命令行 |

最后两行不是配置问题，是权限模型问题。但**半自动和全自动产出的账本是完全一样的**——区别只是「复制粘贴一次」。这就是协议优先的好处：不管对面是哪个模型，只要能吐出符合 SPEC 的 JSON，就能接进来。

典型的一轮对话：

> **你**：`（贴一篇英语作文）` 帮我改改。
>
> **AI**：`（批改，指出 6 处问题）` 记了 5 个新词、2 条错因。最该先背：resilient（写成 resistent）、mitigate（「减轻」整句没译出来）。周报里会出现在「写作搭配」和「环境」两组。

注意最后那句话——**账户里多了东西**，而不是聊完就散。

## 词库怎么用

一个词只有两种状态，没有分数：

```
   add                learn
 ∅ ───► 还没背 ─────► 背过了
          ▲              │
          └──────────────┘
             unlearn
```

- `learn`：背完了，下次报告里不再出现
- `unlearn`：又想不起来了，放回清单，**`note` 不会被清掉**

v2 删掉了 v1 那套「答对 +1、答错 −2、四连对算掌握」的掌握度模型。删掉的原因写在 [SPEC §2](SPEC.md#2-为什么没有掌握度) 里，一句话：**那套模型需要「答题」这个动作来喂数据，而实际使用里根本没有答题环节**——没有测验就没有正确答案，分数会永远停在 0，清单永远不缩小。

## 每周自动出报告

调度**不在脚本里**，交给系统：cron / Windows 计划任务 / launchd，任选其一，一行命令：

```bash
# Linux / macOS，每周日 20:00
0 20 * * 0 cd /path/to/vocab-ledger && python vocab.py weekly --open
```

`weekly` 是幂等的：它只生成「已经过了周日 20:00、但还没生成」的那些周。所以可以放心绑定两个触发器——

- **每周日 20:00**：正常出本周报告
- **每次登录**：检查有没有漏掉的周，有就补上

这样「周日没开机」不再是问题：下周第一次开机，登录触发的那一次会把漏掉的周报补出来，不需要你记得、也不需要等到下周日。

Windows、macOS 的写法，以及「为什么不内置调度器」，见 [docs/scheduling.md](docs/scheduling.md)。

**不确定它到底跑没跑？** 一条命令看全：

```
$ python vocab.py doctor --task VocabLedgerWeekly
生词账本自检 · /path/to/vocab-ledger
────────────────────────────────────────────────────────
✓ Python       3.12.4
✓ 数据文件     data/words.json  (13.0 KB)
✓ 词库         42 个词（还没背 17 / 背过 25）
✓ 最近出报告   2026-11-22 20:00（2026-W47，20 词，2 天前）
✓ 计划任务     VocabLedgerWeekly  Ready  下次 2026-11-29 20:00  上次 2026-11-22 20:00  exit=0
────────────────────────────────────────────────────────
一切正常。
```

报告每次生成都会写一个心跳文件（`logs/last-report.json`），`doctor` 靠它判断「调度还活着吗」，而不是靠你回想。

## 报告里有什么

1. **本周新记** —— 这周记了哪些词，当初为什么记它。
2. **还没背的** —— 按主题分组的卡片：拼写、音标、词性、释义、搭配、例句，**外加你自己写错过的记录**。
3. **本周背完** —— 这周被你划掉的词。这一节是报告里唯一的正反馈。
4. **遮字自测** —— 只给单词和音标，右边留白让你默写，另起一页方便打印。
5. **本周建议** —— 积压太多、或者一周没划掉任何词时，说两句。

A4 排版，卡片不跨页截断，右下角有「打印 / 存为 PDF」。

## 隐私

- **不联网**。除了 `git clone`，它这辈子不会发出一个请求。
- **无账号、无遥测、无云同步**。
- 数据是一个你能用记事本打开的 JSON，摆在你自己的硬盘上。

生词库其实挺私人的——它暴露你的水平、职业方向和阅读兴趣。所以「不上传」不是缺功能，是设计目标。

## 不做什么（Non-goals）

明确拒绝，因为这些都会破坏「零维护」：

- ❌ 内置间隔重复调度器（分数 + 排序已经近似 Leitner，够用）
- ❌ 手机 App、网页后端、账号体系
- ❌ 云同步
- ❌ 音标/释义的自动抓取（AI 在场时由 AI 填，AI 不在时留空）

## 目录

```
vocab-ledger/
├─ vocab.py                     参考实现（零依赖，单文件）
├─ SPEC.md                      协议：格式、判定、渲染契约
├─ AGENTS.md                    给 AI 的仓库内约定
├─ CLAUDE.md                    Claude Code 入口（引用 AGENTS.md）
├─ skills/vocab-ledger/SKILL.md 同一流程的 skill 版
├─ prompts/universal.md         给不能读写文件的 AI 用的提示词
├─ examples/words.sample.json   可直接导入的样例
├─ docs/                        调度说明、预览图
└─ tests/                       42 个标准库单元测试
```

## 开发

```bash
python -m unittest discover -s tests -v     # 零依赖，不用装 pytest
```

## 协议优先

这个项目的第一交付物是 [SPEC.md](SPEC.md)，不是 `vocab.py`。

任何语言、任何工具，只要满足规格，就能读写同一本账本、渲染出同一种报告。欢迎其他实现——用 Node 写个渲染器、用 Go 写个 CLI、给 Obsidian 写个插件来读 `words.json`，都算这个项目的一部分。

参考实现只是其中之一。

## License

MIT
