# 通用提示词 · 给「不能读写文件」的 AI 用

适用于**豆包、网页版 ChatGPT、Kimi、手机 App、任何纯聊天界面**。

它们做不到两件事：读你硬盘上的 `words.json`、执行命令。所以流程拆成两半——**它批改并输出 JSON，你复制粘贴一次入库**。字段和周报跟全自动方案完全一样。

---

## 用法（三步）

1. 把下面**第一段**连同你的作文/翻译一起发给 AI。
2. 把它返回的 JSON 存成 `words.json`（记事本另存为，编码选 UTF-8），放在 `D:\小鲸鱼\英语生词\` 下。
3. 命令行跑一次：

```bat
cd /d D:\小鲸鱼\英语生词
D:\Python\python.exe vocab.py add --file words.json
```

之后每次背完想记录对错，用**第二段**。

---

## 第一段：批改 + 输出生词

```text
你是英语批改助手。请批改我下面这段英语（或中译英），然后按固定格式输出生词。

批改要求：
- 指出语法、搭配、用词、时态、大小写的问题
- 对每个被改的地方，说明「我原来写的是什么」和「应该写什么」

生词收录要求（严格照做）：
1. 只收「我确实不会或写错的」词、短语、搭配。我用对的常见词不要收。
2. 一共收 3 到 8 个，宁少勿多。
3. 每个词给：拼写、音标、词性、中文释义、1-2 条搭配、1 个例句、一个主题标签（2-4 个汉字）。
4. 音标没把握就留空字符串，不要编。
5. note 字段必须写清「我错在哪」，要具体，例如「写成 australian」「介词用 for，应为 to」。禁止写「错了」「不熟」。

输出要求：
- 只输出一个 JSON 数组，不要任何解释、不要 Markdown 代码块标记。
- 字段名必须完全一致：

[
  {
    "word": "astronaut",
    "phonetic": "/ˈæstrənɔːt/",
    "pos": "n.",
    "zh": "航天员",
    "collocations": ["taikonaut 中国航天员"],
    "example": "Yang Liwei became the first Chinese astronaut to travel into space.",
    "tags": ["航天科技"],
    "note": "写成 australian（澳大利亚人）"
  }
]

下面是待批改的内容：
（在这里粘贴你的作文或翻译）
```

## 第二段：背完了，把词划掉

账本只有两种状态：**还没背**、**背过了**。背完一批就标一下，它们下次不再出现在报告里。

不用问 AI，自己一条命令即可：

```bat
D:\Python\python.exe vocab.py learn astronaut mitigate nuance
D:\Python\python.exe vocab.py unlearn astronaut      :: 又忘了，放回去
```

想批量也可以让 AI 把它刚输出的词整理成数组，用 `--file`：

```bat
D:\Python\python.exe vocab.py learn --file words.json
```

`words.json` 只要一个字符串数组，或者 `{"words": ["astronaut", "mitigate"]}`。

---

## 常见问题

**AI 返回的 JSON 外面裹了 ```json 代码块怎么办？**
`vocab.py` 会自动剥掉代码块标记，直接存下来导入即可。

**它多说了几句话怎么办？**
删掉 JSON 前后的文字，只留方括号开始到方括号结束的部分。

**一次批改生成了多个文件怎么办？**
可以重复 `add`，同一个词不会重复出现，也不会覆盖已有的释义（幂等）。

**我想让它按我的主题分类？**
把 `tags` 的候选值告诉它，例如「只用这三个标签：航天科技、写作搭配、环境」。
