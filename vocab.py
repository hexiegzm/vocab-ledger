#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vocab-ledger · 本地生词账本（v2：只记不会的词，不算分）
=========================================================

纯标准库、零依赖、零联网：把「我不会的词」记成一条记录，每周渲染成一张能打印的纸。

v2 的设计（详见 SPEC.md）：
  * 不做掌握度、不算分、不排复习档期；
  * 一个词只有两种状态：还没背 / 背过了；
  * 背完用 `learn` 标一下，下次报告里就不再出现；
  * 唯一不可再生的资产是 `note`——当初为什么记它（写成了什么、哪里错）。

目录::

    vocab-ledger/
      vocab.py              本脚本（唯一必需文件）
      data/words.json       词库（唯一数据源，可直接手改）
      data/backup/          每次写入前的自动备份
      reports/              生成好的 HTML 周报
      logs/                 运行日志

常用命令::

    python vocab.py add --file examples/words.sample.json
    python vocab.py add astronaut --zh 航天员 --pos n. --tag 航天科技 \
        --source "2026-W47 翻译练习" --note "写成 australian"
    python vocab.py learn astronaut mitigate        # 背完了
    python vocab.py unlearn astronaut               # 又忘了，放回去
    python vocab.py list                            # 还没背的
    python vocab.py report --open                   # 出周报
    python vocab.py weekly                          # 补漏 + 出当周（调度用）
    python vocab.py doctor --task VocabLedgerWeekly # 自检
"""

from __future__ import annotations

import argparse
import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import date, datetime, time, timedelta
from pathlib import Path

# ---------------------------------------------------------------- 路径与常量

BASE = Path(__file__).resolve().parent
DATA_FILE = BASE / "data" / "words.json"
BACKUP_DIR = BASE / "data" / "backup"
REPORT_DIR = BASE / "reports"
LOG_DIR = BASE / "logs"

SCHEMA_VERSION = 2
MAX_BACKUPS = 40
STATE_ZH = {"open": "还没背", "learned": "背过了"}


# ---------------------------------------------------------------- 小工具

def force_utf8() -> None:
    """Windows 控制台默认 GBK，统一成 UTF-8，避免 emoji / 中文报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / "vocab.log", "a", encoding="utf-8") as f:
        f.write(f"[{now_iso()}] {msg}\n")


def dw(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - dw(s))


def cut(s: str, width: int) -> str:
    out, acc = [], 0
    for ch in s:
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if acc + w > width:
            return "".join(out) + "…"
        out.append(ch)
        acc += w
    return "".join(out)


def parse_date(s: str | None) -> date:
    if not s:
        return date.today()
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise SystemExit(f"日期格式应为 YYYY-MM-DD，收到：{s}")


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def parse_week(s: str | None) -> date:
    if not s:
        return week_monday(date.today())
    s = s.strip()
    if "-W" in s.upper():
        try:
            y, w = s.upper().split("-W")
            return date.fromisocalendar(int(y), int(w), 1)
        except ValueError:
            raise SystemExit(f"周格式应为 2026-W47，收到：{s}")
    return week_monday(parse_date(s))


def in_range(iso: str | None, start: date, end: date) -> bool:
    return bool(iso) and start.isoformat() <= iso <= end.isoformat()


def read_json(path: Path):
    """读 JSON，容忍 BOM 和 ```json 代码块包裹（聊天机器人最爱加这个）。"""
    text = path.read_text(encoding="utf-8-sig").strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl != -1 else ""
        text = text.rstrip()
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


# ---------------------------------------------------------------- 数据读写

def migrate(data: dict) -> dict:
    """把 v1（带 score / status 的掌握度模型）迁到 v2（只有 open / learned）。"""
    if data.get("version", 1) >= SCHEMA_VERSION:
        return data
    for w in data.get("words", []):
        was_mastered = w.get("status") == "mastered"
        w["learned"] = bool(was_mastered)
        w["learned_at"] = w.get("last_review") if was_mastered else None
        if not w.get("note"):
            notes = [h.get("note") for h in w.get("history", [])
                     if h.get("event") in ("wrong", "misspelled") and h.get("note")]
            if notes:
                w["note"] = notes[-1]
        for dead in ("score", "consecutive", "status", "last_review", "history"):
            w.pop(dead, None)
    data["version"] = SCHEMA_VERSION
    return data


def load() -> dict:
    if not DATA_FILE.exists():
        return {"version": SCHEMA_VERSION, "updated_at": None, "words": []}
    with open(DATA_FILE, encoding="utf-8-sig") as f:
        data = json.load(f)
    data.setdefault("words", [])
    for w in data["words"]:
        w.setdefault("collocations", [])
        w.setdefault("tags", [])
        w.setdefault("learned", False)
        w.setdefault("learned_at", None)
        w.setdefault("starred", False)
    return migrate(data)


def save(data: dict, backup: bool = True) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = SCHEMA_VERSION
    data["updated_at"] = now_iso()
    if backup and DATA_FILE.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(DATA_FILE, BACKUP_DIR / f"words-{stamp}.json")
        for p in sorted(BACKUP_DIR.glob("words-*.json"))[:-MAX_BACKUPS]:
            p.unlink(missing_ok=True)
    tmp = DATA_FILE.with_name("words.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DATA_FILE)


def find_word(data: dict, word: str) -> dict | None:
    target = word.strip().lower()
    for w in data["words"]:
        if w["word"].strip().lower() == target:
            return w
    return None


def upsert(data: dict, rec: dict) -> tuple[dict, bool]:
    """新增或合并一条词。返回 (记录, 是否新建)。只补空缺，不覆盖已有内容。"""
    word = (rec.get("word") or "").strip()
    if not word:
        raise SystemExit("词条缺少 word 字段")
    existing = find_word(data, word)
    created = existing is None
    if created:
        existing = {
            "word": word,
            "phonetic": "",
            "pos": "",
            "zh": "",
            "collocations": [],
            "example": "",
            "tags": [],
            "source": "",
            "first_seen": rec.get("first_seen") or date.today().isoformat(),
            "note": "",
            "learned": False,
            "learned_at": None,
            "starred": False,
        }
        data["words"].append(existing)

    w = existing
    for field in ("phonetic", "pos", "zh", "example"):
        if not w.get(field) and rec.get(field):
            w[field] = rec[field]
    if rec.get("note") and not w.get("note"):
        w["note"] = rec["note"]
    if rec.get("source") and rec["source"] not in (w.get("source") or ""):
        w["source"] = (w["source"] + "；" + rec["source"]).strip("；") if w.get("source") else rec["source"]
    for lst in ("collocations", "tags"):
        incoming = rec.get(lst) or []
        if isinstance(incoming, str):
            incoming = [incoming]
        for item in incoming:
            item = str(item).strip()
            if item and item not in w[lst]:
                w[lst].append(item)
    return w, created


# ---------------------------------------------------------------- 命令：add

def cmd_add(args) -> int:
    data = load()
    records: list[dict] = []

    if args.file:
        path = Path(args.file)
        if not path.exists():
            raise SystemExit(f"找不到文件：{path}")
        payload = read_json(path)
        if isinstance(payload, dict):
            payload = payload.get("words", [])
        if not isinstance(payload, list):
            raise SystemExit("批量文件应为数组，或 {\"words\": [...]} 结构")
        records.extend(payload)
    if args.word:
        records.append({
            "word": args.word,
            "zh": args.zh,
            "phonetic": args.phonetic,
            "pos": args.pos,
            "collocations": args.colloc,
            "example": args.example,
            "tags": args.tag,
            "source": args.source,
            "note": args.note,
            "first_seen": args.date,
        })
    if not records:
        raise SystemExit("没有要添加的内容：用 --file 或直接给单词。")

    new = merged = 0
    for rec in records:
        _, created = upsert(data, rec)
        new += created
        merged += not created

    save(data)
    log(f"add: new={new} merged={merged} src={args.file or args.word}")
    print(f"✅ 新增 {new} 个，合并更新 {merged} 个；词库共 {len(data['words'])} 个。")
    return 0


# ---------------------------------------------------------------- 命令：learn / unlearn

def _collect_words(args) -> list[str]:
    words: list[str] = []
    if args.file:
        payload = read_json(Path(args.file))
        if isinstance(payload, dict):
            payload = payload.get("words", [])
        for item in payload:
            words.append(item if isinstance(item, str) else item.get("word", ""))
    words.extend(args.words or [])
    return [w for w in (x.strip() for x in words) if w]


def cmd_learn(args) -> int:
    data = load()
    targets = _collect_words(args)
    if not targets:
        raise SystemExit("没指定要标记的词。")
    stamp = parse_date(args.date).isoformat()
    done, missing = [], []
    for word in targets:
        w = find_word(data, word)
        if w is None:
            missing.append(word)
            continue
        if not w.get("learned"):
            w["learned"] = True
            w["learned_at"] = stamp
            done.append(w["word"])
    if done:
        save(data)
        log(f"learn: {done}")
    print(f"✅ 标记 {len(done)} 个为「背过了」；还剩 {sum(1 for w in data['words'] if not w.get('learned'))} 个没背。")
    if missing:
        print(f"⚠️  词库里没有：{', '.join(missing)}（先用 add 添加）", file=sys.stderr)
        return 2
    return 0


def cmd_star(args) -> int:
    data = load()
    targets = _collect_words(args)
    if not targets:
        raise SystemExit("没指定要收藏的词。")
    done, missing = [], []
    for word in targets:
        w = find_word(data, word)
        if w is None:
            missing.append(word)
            continue
        if not w.get("starred"):
            w["starred"] = True
            done.append(w["word"])
    if done:
        save(data)
        log(f"star: {done}")
    total = sum(1 for w in data["words"] if w.get("starred"))
    print(f"⭐ 收藏 {len(done)} 个；收藏夹共 {total} 个。")
    if missing:
        print(f"⚠️  词库里没有：{', '.join(missing)}（先用 add 添加）", file=sys.stderr)
        return 2
    return 0


def cmd_unstar(args) -> int:
    data = load()
    targets = _collect_words(args)
    done = []
    for word in targets:
        w = find_word(data, word)
        if w and w.get("starred"):
            w["starred"] = False
            done.append(w["word"])
    if done:
        save(data)
        log(f"unstar: {done}")
    total = sum(1 for w in data["words"] if w.get("starred"))
    print(f"取消收藏 {len(done)} 个；收藏夹还剩 {total} 个。")
    return 0


def cmd_unlearn(args) -> int:
    data = load()
    targets = _collect_words(args)
    done = []
    for word in targets:
        w = find_word(data, word)
        if w and w.get("learned"):
            w["learned"] = False
            w["learned_at"] = None
            done.append(w["word"])
    if done:
        save(data)
        log(f"unlearn: {done}")
    print(f"↩️  放回 {len(done)} 个；现在还有 {sum(1 for w in data['words'] if not w.get('learned'))} 个没背。")
    return 0


# ---------------------------------------------------------------- 命令：list / stats / weeks

def cmd_list(args) -> int:
    data = load()
    words = data["words"]
    if args.starred:
        words = [w for w in words if w.get("starred")]
    elif args.learned:
        words = [w for w in words if w.get("learned")]
    elif not args.all:
        words = [w for w in words if not w.get("learned")]
    if args.tag:
        words = [w for w in words if args.tag in (w.get("tags") or [])]
    if args.week:
        mon = parse_week(args.week)
        sun = mon + timedelta(days=6)
        words = [w for w in words if in_range(w.get("first_seen"), mon, sun)]

    if args.json:
        print(json.dumps(words, ensure_ascii=False, indent=2))
        return 0
    if not words:
        print("（没有符合条件的词）")
        return 0

    words = sorted(words, key=lambda w: (w.get("first_seen") or "", w["word"].lower()))
    print(f"{pad('单词', 22)}{pad('状态', 8)}{pad('释义', 28)}标签")
    print("-" * 88)
    for w in words:
        star = "★ " if w.get("starred") else ""
        print(
            f"{pad(star + w['word'], 22)}"
            f"{pad(STATE_ZH['learned' if w.get('learned') else 'open'], 8)}"
            f"{pad(cut(w.get('zh') or '—', 26), 28)}"
            f"{'/'.join(w.get('tags') or []) or '—'}"
        )
    print("-" * 88)
    print(f"共 {len(words)} 个")
    return 0


def cmd_stats(args) -> int:
    data = load()
    words = data["words"]
    mon = week_monday(date.today())
    sun = mon + timedelta(days=6)
    new_this = [w for w in words if in_range(w.get("first_seen"), mon, sun)]
    learned_this = [w for w in words if in_range(w.get("learned_at"), mon, sun)]
    open_count = sum(1 for w in words if not w.get("learned"))
    print(f"词库总量     {len(words)}")
    print(f"还没背       {open_count}")
    print(f"背过了       {len(words) - open_count}")
    print(f"本周新增     {len(new_this)}   （{week_key(mon)} {mon}~{sun}）")
    print(f"本周背完     {len(learned_this)}")
    print(f"收藏夹       {sum(1 for w in words if w.get('starred'))}")
    return 0


def cmd_weeks(args) -> int:
    data = load()
    buckets: dict[str, int] = {}
    for w in data["words"]:
        buckets[week_key(parse_date(w.get("first_seen")))] = \
            buckets.get(week_key(parse_date(w.get("first_seen"))), 0) + 1
    for wk in sorted(buckets):
        mon = parse_week(wk)
        print(f"{wk}  {mon} ~ {mon + timedelta(days=6)}   新增 {buckets[wk]:3d} 个")
    return 0


# ---------------------------------------------------------------- HTML 周报

CSS = """
:root{
  --ink:#1c1b19; --muted:#736d64; --line:#e4dfd4; --paper:#fffdf8;
  --accent:#14604f; --accent-soft:#e9f2ed;
  --warn:#a8401d; --warn-soft:#fbeee7;
  --amber:#8a5a00; --amber-soft:#fdf4de;
}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact; print-color-adjust:exact}
body{
  margin:0; padding:28px 18px 60px; background:#f2efe7; color:var(--ink);
  font-family:"Segoe UI","Microsoft YaHei","PingFang SC",system-ui,sans-serif;
  line-height:1.65; font-size:15px;
}
.page{
  max-width:880px; margin:0 auto; background:var(--paper);
  border:1px solid var(--line); border-radius:10px;
  padding:40px 44px 48px; box-shadow:0 2px 10px rgba(60,50,30,.06);
}
header{border-bottom:3px double var(--line); padding-bottom:18px; margin-bottom:26px}
.kicker{font-size:12px; letter-spacing:.18em; color:var(--accent); font-weight:700; text-transform:uppercase}
h1{font-size:30px; margin:6px 0 4px; letter-spacing:.01em; font-weight:700}
.meta{font-size:12.5px; color:var(--muted)}
.stats{display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:0 0 30px}
.stat{background:var(--accent-soft); border:1px solid #d8e6de; border-radius:8px; padding:12px 10px; text-align:center}
.stat .n{font-size:24px; font-weight:700; color:var(--accent); line-height:1.2}
.stat .l{font-size:11.5px; color:var(--muted); letter-spacing:.04em}
h2{font-size:17px; margin:34px 0 12px; padding-left:10px; border-left:4px solid var(--accent); line-height:1.35}
h2 .count{font-size:12px; color:var(--muted); font-weight:400; margin-left:6px}
.hint{font-size:12.5px; color:var(--muted); margin:-4px 0 14px}
table{width:100%; border-collapse:collapse; font-size:13.5px}
th,td{text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top}
th{font-size:11.5px; letter-spacing:.08em; color:var(--muted); font-weight:600; background:#faf7f0}
td.w{font-family:Georgia,"Cambria",serif; font-size:16px; font-weight:600}
.err-bad{color:var(--warn)}
.group{margin-bottom:22px}
.group-head{display:flex; align-items:baseline; gap:10px; margin:18px 0 10px; padding-bottom:6px; border-bottom:1px dashed var(--line)}
.group-head .t{font-size:14.5px; font-weight:700; color:var(--accent)}
.group-head .c{font-size:11.5px; color:var(--muted)}
.cards{display:grid; grid-template-columns:1fr 1fr; gap:12px}
.card{border:1px solid var(--line); border-radius:8px; padding:12px 14px; background:#fffefa; break-inside:avoid}
.card-head{display:flex; align-items:baseline; flex-wrap:wrap; gap:8px; margin-bottom:4px}
.card-head .w{font-family:Georgia,"Cambria",serif; font-size:19px; font-weight:700; letter-spacing:.01em}
.card-head .ph{font-size:12px; color:var(--muted); font-family:"Charis SIL","Gentium Plus","Lucida Sans Unicode","Segoe UI","Microsoft YaHei",sans-serif}
.card-head .pos{font-size:11.5px; color:var(--muted); font-style:italic}
.card .zh{font-size:14.5px; font-weight:600; margin:2px 0 6px}
.colloc{margin:0 0 6px; padding-left:16px; font-size:12.5px; color:#3f3a33}
.colloc li{margin:1px 0}
.eg{font-size:12.5px; color:var(--muted); font-style:italic; border-left:2px solid var(--line); padding-left:10px}
.myerr{margin-top:7px; font-size:12px; color:var(--warn); background:var(--warn-soft); border-radius:5px; padding:3px 8px; display:inline-block}
.chips{display:flex; flex-wrap:wrap; gap:8px}
.chip{border:1px solid #cfe0d6; background:var(--accent-soft); color:#14503f; border-radius:99px; padding:3px 12px; font-size:12.5px}
.chip .s{font-family:Georgia,serif; font-weight:600}
.tips{margin:0; padding-left:18px; font-size:13px; color:#3f3a33}
.tips li{margin:4px 0}

.grid{font-size:13px}
.grid th{padding:7px 8px}
.grid td{padding:7px 8px; border-bottom:1px solid var(--line)}
.grid .small{font-size:12.5px; color:#3f3a33}
.grid .tagcell{font-size:12px; color:var(--accent); white-space:nowrap}
.grid .date{white-space:nowrap; color:var(--muted)}
.star{color:#c9962b; margin-right:3px; font-size:13px}
.grid tbody tr:nth-child(even){background:#fcfaf4}
.grid td.w{font-size:15.5px}
input.search{
  width:100%; padding:9px 12px; font:inherit; font-size:13px; color:var(--ink);
  border:1px solid var(--line); border-radius:8px; background:#fffefa; outline:none;
}
input.search:focus{border-color:var(--accent)}
.quiz td{height:44px; border-bottom:1px solid #cfc8ba}
.quiz td.blank{border-bottom:1px solid #b9b1a1}
footer{margin-top:40px; padding-top:14px; border-top:1px solid var(--line); font-size:11.5px; color:var(--muted)}
.toolbar{position:fixed; right:22px; bottom:22px}
.toolbar button{
  font:inherit; font-size:13px; padding:10px 18px; border-radius:99px; cursor:pointer;
  border:1px solid var(--accent); background:var(--accent); color:#fff; box-shadow:0 3px 10px rgba(0,0,0,.15)
}
.empty{color:var(--muted); font-size:13px; padding:10px 0}
@media print{
  body{background:#fff; padding:0; font-size:11.5pt}
  .page{max-width:none; border:0; box-shadow:none; padding:0; border-radius:0}
  .no-print{display:none !important}
  .page-break{break-before:page}
  h2{break-after:avoid}
  .card{break-inside:avoid}
  @page{size:A4; margin:15mm 14mm}
}
@media (max-width:640px){
  .cards{grid-template-columns:1fr}
  .stats{grid-template-columns:repeat(2,1fr)}
  .page{padding:24px 18px}
}
"""


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def _wcell(w: dict) -> str:
    star = '<span class="star">★</span>' if w.get("starred") else ""
    return f'<td class="w">{star}{esc(w["word"])}</td>'


def _cell(w: dict, text: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f"<td{c}>{text}</td>"


def render_html(data: dict, monday: date, max_review: int = 60, title: str | None = None) -> str:
    sunday = monday + timedelta(days=6)
    wk = week_key(monday)
    wnum = wk.split("-W")[1]
    words = data["words"]

    week_words = sorted([w for w in words if in_range(w.get("first_seen"), monday, sunday)],
                        key=lambda w: w["word"].lower())
    open_words = [w for w in words if not w.get("learned")]
    # 词库表：按标签分组（未分类最后），组内按字母，方便检索
    open_words.sort(key=lambda w: (
        (w.get("tags") or ["未分类"])[0] == "未分类",
        (w.get("tags") or ["未分类"])[0],
        w["word"].lower(),
    ))
    learned_week = sorted([w for w in words if in_range(w.get("learned_at"), monday, sunday)],
                          key=lambda w: w["word"].lower())
    shown = open_words[:max_review]

    h = []
    h.append("<!DOCTYPE html>")
    h.append('<html lang="zh-CN"><head><meta charset="utf-8">')
    h.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    h.append(f"<title>{esc(title or f'第 {int(wnum)} 周 生词报告')}</title>")
    h.append(f"<style>{CSS}</style></head><body>")
    h.append('<div class="toolbar no-print"><button onclick="window.print()">打印 / 存为 PDF</button></div>')
    h.append('<div class="page">')

    h.append("<header>")
    h.append('<div class="kicker">English Vocabulary · Weekly</div>')
    h.append(f"<h1>第 {int(wnum)} 周 · 生词报告</h1>")
    h.append(f'<div class="meta">{monday.isoformat()} — {sunday.isoformat()} · {wk} · 生成于 {now_iso()}</div>')
    h.append("</header>")

    h.append('<div class="stats">')
    for num, label in (
        (len(week_words), "本周新增"),
        (len(open_words), "还没背"),
        (len(learned_week), "本周背完"),
        (len(words), "词库总量"),
    ):
        h.append(f'<div class="stat"><div class="n">{num}</div><div class="l">{label}</div></div>')
    h.append("</div>")

    # ---------------- 表一：本周新增 ----------------
    h.append(f'<h2>表一 · 本周新增 <span class="count">{len(week_words)} 词</span></h2>')
    h.append('<p class="hint">只装这一周记下来的词，是本周要背的全部内容。</p>')
    if week_words:
        h.append('<table class="grid"><thead><tr>'
                 '<th style="width:16%">单词</th><th style="width:14%">音标</th>'
                 '<th style="width:20%">释义</th><th style="width:24%">搭配</th>'
                 '<th style="width:16%">为什么记它</th><th style="width:10%">日期</th></tr></thead><tbody>')
        for w in week_words:
            h.append(
                f'<tr>' + _wcell(w)
                + _cell(w, esc(w.get("phonetic") or ""), "ph")
                + _cell(w, esc(w.get("zh") or "—"))
                + _cell(w, esc("；".join(w.get("collocations") or []) or "—"), "small")
                + _cell(w, esc(w.get("note") or "—"), "err-bad small")
                + _cell(w, esc(w.get("first_seen") or ""), "small date")
                + "</tr>"
            )
        h.append("</tbody></table>")
    else:
        h.append('<div class="empty">这周没有新记的词。</div>')

    # ---------------- 表二：不会的词库 ----------------
    count_txt = (f"{len(shown)} / {len(open_words)} 词（超出上限未列出）"
                 if len(open_words) > len(shown) else f"{len(open_words)} 词")
    h.append(f'<h2>表二 · 不会的词库 <span class="count">{count_txt}</span></h2>')
    h.append('<p class="hint">全部还没背的词，按主题分组、组内按字母排。背完一个就说一句「XX 会了」，它会从这里消失。</p>')
    if shown:
        search_js = (
            "var v=this.value.toLowerCase();"
            "document.querySelectorAll('#lib tbody tr').forEach(function(r){"
            "r.style.display=r.textContent.toLowerCase().indexOf(v)>-1?'':'none'});"
        )
        h.append('<div class="no-print" style="margin:-6px 0 10px">'
                 '<input class="search" id="q" type="search" '
                 'placeholder="在词库里搜单词或释义…" '
                 f'oninput="{search_js}">'
                 "</div>")
        h.append('<table class="grid" id="lib"><thead><tr>'
                 '<th style="width:18%">单词</th><th style="width:15%">音标</th>'
                 '<th style="width:20%">释义</th><th style="width:26%">搭配</th>'
                 '<th style="width:12%">标签</th><th style="width:11%">记录于</th></tr></thead><tbody>')
        for w in shown:
            h.append(
                f'<tr>' + _wcell(w)
                + _cell(w, esc(w.get("phonetic") or ""), "ph")
                + _cell(w, esc(w.get("zh") or "—"))
                + _cell(w, esc("；".join(w.get("collocations") or []) or "—"), "small")
                + _cell(w, esc((w.get("tags") or ["未分类"])[0]), "tagcell")
                + _cell(w, esc(w.get("first_seen") or ""), "small date")
                + "</tr>"
            )
        h.append("</tbody></table>")
    else:
        h.append('<div class="empty">词库里没有待背的词，干净了。</div>')
    if len(open_words) > len(shown):
        h.append(f'<p class="hint">另有 {len(open_words) - len(shown)} 个词未列入本期（上限 {max_review}），'
                 f'可用 <code>vocab.py list</code> 查看，或调大 <code>--max-review</code>。</p>')

    # ---------------- 收藏夹 ----------------
    starred = sorted([w for w in words if w.get("starred")], key=lambda w: w["word"].lower())
    h.append(f'<h2>收藏夹 <span class="count">{len(starred)} 词</span></h2>')
    h.append('<p class="hint">★ 是你特意留下的词，斩掉之后仍然在这里。写作文前扫一眼。</p>')
    if starred:
        h.append('<table class="grid"><thead><tr>'
                 '<th style="width:18%">单词</th><th style="width:14%">音标</th>'
                 '<th style="width:22%">释义</th><th style="width:32%">搭配</th>'
                 '<th style="width:14%">状态</th></tr></thead><tbody>')
        for w in starred:
            h.append(
                f'<tr>' + _wcell(w)
                + _cell(w, esc(w.get("phonetic") or ""), "ph")
                + _cell(w, esc(w.get("zh") or "—"))
                + _cell(w, esc("；".join(w.get("collocations") or []) or "—"), "small")
                + _cell(w, "斩了" if w.get("learned") else "还没背", "tagcell")
                + "</tr>"
            )
        h.append("</tbody></table>")
    else:
        h.append('<div class="empty">还没有收藏的词。背的时候觉得「这个写作能用」，说一句「收藏 XX」就行。</div>')

    # ---------------- 斩词 ----------------
    killed_total = len(words) - len(open_words)
    h.append(f'<h2>斩词 <span class="count">本周 {len(learned_week)} 词 · 累计 {killed_total} 词</span></h2>')
    if learned_week:
        h.append('<div class="chips">')
        for w in learned_week:
            h.append(f'<span class="chip"><span class="s">{esc(w["word"])}</span> {esc(w.get("zh") or "")}</span>')
        h.append("</div>")
    else:
        h.append('<div class="empty">这周还没斩过词。背完说一句「XX 斩了」，它就从表二消失。</div>')

    # ---------------- 遮字自测 ----------------
    h.append('<h2 class="page-break">遮字自测 <span class="count">盖上右列，逐词默写</span></h2>')
    h.append('<p class="hint">用表二的词抽查。看单词和音标，写出释义与搭配；写完翻回上一页对表。</p>')
    if shown:
        h.append('<table class="quiz"><thead><tr><th style="width:26%">单词</th><th style="width:20%">音标</th>'
                 '<th style="width:54%">释义 / 搭配（自己写）</th></tr></thead><tbody>')
        for w in shown:
            star = '<span class="star">★</span>' if w.get("starred") else ""
            h.append(
                f'<tr><td class="w">{star}{esc(w["word"])}</td>'
                f'<td class="ph">{esc(w.get("phonetic") or "")}</td>'
                f'<td class="blank"></td></tr>'
            )
        h.append("</tbody></table>")
    else:
        h.append('<div class="empty">—</div>')

    # ---------------- 建议 ----------------
    tips = []
    if week_words:
        tips.append(f"表一有 {len(week_words)} 个词，先把这张表读完，再扫一眼表二里有没有老朋友。")
    if len(open_words) > 40:
        tips.append(f"词库已积到 {len(open_words)} 个，建议每周只新增 10–15 个，否则表二会越来越长。")
    if not learned_week:
        tips.append("这周一个词都没标「背过了」。背完说一句，不然表二只会变长。")
    oldest = min(open_words, key=lambda w: w.get("first_seen") or "9999") if open_words else None
    if oldest:
        tips.append(f"最早没背的词是 {oldest['word']}（{oldest.get('first_seen')} 记的），别让它跨月。")
    if tips:
        h.append('<h2>本周建议</h2><ul class="tips">')
        h.extend(f"<li>{esc(t)}</li>" for t in tips)
        h.append("</ul>")

    h.append(f'<footer>由 <code>vocab.py</code> 生成 · 数据源 data/words.json · '
             f'共 {len(words)} 词 · 下期自动生成于每周日 20:00</footer>')
    h.append("</div></body></html>")
    return "\n".join(h)


# ---------------------------------------------------------------- 命令：report / weekly

def write_report(data: dict, monday: date, out: Path, max_review: int = 60,
                 title: str | None = None) -> Path:
    doc = render_html(data, monday, max_review=max_review, title=title)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    log(f"report: {out} week={week_key(monday)} words={len(data['words'])}")

    heartbeat = LOG_DIR / "last-report.json"
    previous = None
    if heartbeat.exists():
        try:
            previous = json.loads(heartbeat.read_text(encoding="utf-8")).get("week")
        except Exception:
            previous = None
    if previous and parse_week(previous) > monday:
        return out
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.write_text(json.dumps({
        "at": now_iso(), "week": week_key(monday),
        "words": len(data["words"]), "path": str(out),
    }, ensure_ascii=False), encoding="utf-8")
    return out


def cmd_report(args) -> int:
    data = load()
    monday = parse_week(args.week)
    out = Path(args.out) if args.out else REPORT_DIR / f"vocab-{week_key(monday)}.html"
    write_report(data, monday, out, args.max_review, args.title)
    print(f"✅ 周报已生成：{out}")
    if args.open:
        open_path(out)
    return 0


def open_path(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:  # pragma: no cover
        print(f"（自动打开失败：{exc}，请手动打开 {path}）", file=sys.stderr)


def last_reported_week() -> date | None:
    weeks: list[date] = []
    heartbeat = LOG_DIR / "last-report.json"
    if heartbeat.exists():
        try:
            wk = json.loads(heartbeat.read_text(encoding="utf-8")).get("week")
            if wk:
                weeks.append(parse_week(wk))
        except Exception:
            pass
    if REPORT_DIR.exists():
        for p in REPORT_DIR.glob("vocab-*.html"):
            m = re.match(r"vocab-(\d{4}-W\d{2})", p.name)
            if m:
                try:
                    weeks.append(parse_week(m.group(1)))
                except Exception:
                    pass
    return max(weeks) if weeks else None


def due_weeks(last: date | None, current: date, now: datetime, max_weeks: int = 8) -> list[date]:
    """某周的周报，在「该周周日 20:00」之后就该有了。返回所有已到期但还没出的周。"""
    start = (last + timedelta(weeks=1)) if last else (current - timedelta(weeks=max_weeks - 1))
    floor = current - timedelta(weeks=max_weeks - 1)
    if start < floor:
        start = floor
    result: list[date] = []
    monday = start
    while monday <= current:
        if now >= datetime.combine(monday + timedelta(days=6), time(20, 0)):
            result.append(monday)
        monday += timedelta(weeks=1)
    return result


def cmd_weekly(args) -> int:
    """补漏 + 出当周。幂等：没有到期的周就什么都不做。"""
    data = load()
    now = datetime.now()
    current = week_monday(now.date())
    pending = due_weeks(last_reported_week(), current, now, args.max_weeks)
    seen = [parse_date(w.get("first_seen")) for w in data["words"] if w.get("first_seen")]
    if seen:
        earliest = week_monday(min(seen))
        pending = [m for m in pending if m >= earliest]
    if not pending:
        print("本周已是最新，无需生成。")
        return 0
    made: list[Path] = []
    for monday in pending:
        out = REPORT_DIR / f"vocab-{week_key(monday)}.html"
        write_report(data, monday, out, args.max_review)
        made.append(out)
        print(f"✅ {week_key(monday)} {out}")
    backfilled = sum(1 for m in pending if m < current)
    if backfilled == len(made):
        print(f"（补生成了 {backfilled} 份历史周报）")
    elif backfilled:
        print(f"（含 {backfilled} 份补生成的历史周报）")
    if args.open:
        open_path(made[-1])
    return 0


# ---------------------------------------------------------------- 命令：doctor

def task_info(name: str) -> dict:
    if not sys.platform.startswith("win"):
        return {"error": "当前不是 Windows，跳过计划任务检查"}
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "_-")
    if not safe:
        return {"error": "任务名不合法"}
    ps = (
        f"$t = Get-ScheduledTask -TaskName '{safe}' -ErrorAction Stop; "
        "$i = $t | Get-ScheduledTaskInfo; "
        "$n = ''; if ($i.NextRunTime) { $n = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm') }; "
        "$l = ''; if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $l = $i.LastRunTime.ToString('yyyy-MM-dd HH:mm') }; "
        "[pscustomobject]@{State=[string]$t.State; Next=$n; Last=$l; Code=[int]$i.LastTaskResult} "
        "| ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return {"error": f"查询失败：{exc}"}
    if r.returncode != 0:
        msg = (r.stderr or "").strip().splitlines()
        return {"error": msg[0][:160] if msg else "任务不存在或无权访问"}
    try:
        return json.loads(r.stdout.strip())
    except Exception:
        return {"error": "无法解析计划任务输出"}


def cmd_doctor(args) -> int:
    rows: list[tuple[str, str, str]] = []
    problems = warnings = 0

    def add(level: str, label: str, value: str) -> None:
        nonlocal problems, warnings
        if level == "bad":
            problems += 1
        elif level == "warn":
            warnings += 1
        rows.append((level, label, value))

    add("ok" if sys.version_info >= (3, 10) else "warn", "Python",
        platform.python_version() + ("" if sys.version_info >= (3, 10) else "（建议 3.10+）"))

    if DATA_FILE.exists():
        add("ok", "数据文件", f"{DATA_FILE}  ({DATA_FILE.stat().st_size / 1024:.1f} KB)")
    else:
        add("warn", "数据文件", f"{DATA_FILE} 不存在（还没有词）")

    try:
        data = load()
        words = data["words"]
        opened = sum(1 for w in words if not w.get("learned"))
        add("ok", "词库", f"{len(words)} 个词（还没背 {opened} / 背过 {len(words) - opened}）")
        broken = [w for w in words if not w.get("word") or not w.get("zh")]
        if broken:
            add("warn", "字段完整性", f"{len(broken)} 个词缺拼写或释义")
    except Exception as exc:
        add("bad", "读取词库", f"失败：{exc}")

    heartbeat = LOG_DIR / "last-report.json"
    if heartbeat.exists():
        try:
            info = json.loads(heartbeat.read_text(encoding="utf-8"))
            when = datetime.strptime(info["at"], "%Y-%m-%d %H:%M")
            age = (datetime.now() - when).days
            add("ok" if age <= 8 else "warn", "最近出报告",
                f"{info['at']}（{info.get('week')}，{info.get('words')} 词，{age} 天前）")
        except Exception:
            add("warn", "最近出报告", "心跳文件存在但无法解析")
    else:
        add("warn", "最近出报告", "还没有生成过任何报告，跑一次 report 试试")

    if args.task:
        info = task_info(args.task)
        if "error" in info:
            add("bad", "计划任务", f"{args.task}：{info['error']}")
        else:
            code = info.get("Code", -1)
            add("ok" if code == 0 else "warn", "计划任务",
                f"{args.task}  {info.get('State')}  下次 {info.get('Next') or '—'}  "
                f"上次 {info.get('Last') or '从未运行'}  exit={code}")

    icon = {"ok": "✓", "warn": "⚠", "bad": "✗"}
    width = max(dw(r[1]) for r in rows) + 3
    print(f"生词账本自检 · {BASE}")
    print("─" * 64)
    for level, label, value in rows:
        for i, line in enumerate(value.split("\n")):
            head = f"{icon[level]} {pad(label, width)}" if i == 0 else " " * (width + 2)
            print(f"{head}{line}")
    print("─" * 64)
    if problems:
        print(f"{problems} 个问题需要处理，{warnings} 个提醒。")
        return 1
    if warnings:
        print(f"可用，但有 {warnings} 个提醒。")
        return 0
    print("一切正常。")
    return 0



# ---------------------------------------------------------------- 命令：review（本地背词页）

REVIEW_CSS = """
body.review{background:#f2efe7; padding:26px 18px 90px; font-size:15px}
.rev-wrap{max-width:1080px; margin:0 auto}
.rev-head{display:flex; align-items:flex-end; gap:18px; flex-wrap:wrap; margin-bottom:16px}
.rev-head h1{font-size:26px; margin:2px 0 0}
.rev-head .meta{font-size:12px; color:var(--muted)}
.tabs{display:flex; gap:8px; margin-left:auto; flex-wrap:wrap}
.tabs button{
  font:inherit; font-size:13.5px; padding:8px 14px; border-radius:99px; cursor:pointer;
  border:1px solid var(--line); background:#fffefa; color:#3f3a33;
}
.tabs button b{font-weight:600; margin-left:4px; color:var(--accent)}
.tabs button.on{background:var(--accent); border-color:var(--accent); color:#fff}
.tabs button.on b{color:#fff}
.rev-bar{display:flex; gap:10px; align-items:center; margin:0 0 22px}
.rev-bar .search{flex:1}
.rev-bar .hint{font-size:12px; color:var(--muted); white-space:nowrap}
.cards.rev{grid-template-columns:1fr 1fr; gap:14px}
.card.rev{position:relative; padding:16px 18px 14px; transition:opacity .18s, transform .18s}
.card.rev.active{outline:2px solid var(--accent); outline-offset:2px}
.card.rev.leaving{opacity:0; transform:scale(.97)}
.acts{position:absolute; right:12px; top:12px; display:flex; gap:6px}
.act{
  font:inherit; font-size:12px; line-height:1; padding:7px 10px; border-radius:8px; cursor:pointer;
  border:1px solid var(--line); background:#fff; color:#6f6a63; min-width:34px;
}
.act:hover{border-color:#c9c2b4}
.act.star.on{background:#fdf3dd; border-color:#e3c27a; color:#8a5a00}
.act.kill.on{background:var(--accent-soft); border-color:#cfe0d6; color:var(--accent)}
.card .revmeta{font-size:11.5px; color:var(--muted); margin-bottom:6px}
.card .w{font-size:22px}
.empty-rev{color:var(--muted); font-size:14px; padding:30px 4px}
"""

REVIEW_JS = r"""
const cnt = {
  open: document.getElementById('cnt-open'),
  star: document.getElementById('cnt-star'),
  kill: document.getElementById('cnt-kill'),
};
let tab = 'open';

function cards(){ return [...document.querySelectorAll('.card.rev')]; }

function recount(){
  const all = cards();
  cnt.open.textContent = all.filter(c => c.dataset.learn === '0').length;
  cnt.star.textContent = all.filter(c => c.dataset.star === '1').length;
  cnt.kill.textContent = all.filter(c => c.dataset.learn === '1').length;
}

function match(card){
  if (tab === 'open' && card.dataset.learn !== '0') return false;
  if (tab === 'starred' && card.dataset.star !== '1') return false;
  if (tab === 'learned' && card.dataset.learn !== '1') return false;
  const q = document.getElementById('q').value.trim().toLowerCase();
  if (q && !card.textContent.toLowerCase().includes(q)) return false;
  return true;
}

function apply(){
  const all = cards();
  let first = null;
  all.forEach(c => {
    const ok = match(c);
    c.style.display = ok ? '' : 'none';
    c.classList.remove('active');
    if (ok && !first) first = c;
  });
  if (first) first.classList.add('active');
  document.getElementById('empty').style.display = all.some(match) ? 'none' : '';
}

async function act(card, field){
  const value = card.dataset[field] !== '1';
  const body = { word: card.dataset.word, field: field === 'star' ? 'star' : 'learn', value: value };
  try {
    const r = await fetch('/api/mark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
  } catch (e) {
    alert('写入失败：' + e.message);
    return;
  }
  card.dataset[field] = value ? '1' : '0';
  const btn = card.querySelector('.act.' + field);
  btn.classList.toggle('on', value);
  btn.textContent = field === 'star' ? (value ? '★' : '☆') : (value ? '斩了' : '斩');
  card.classList.add('leaving');
  setTimeout(() => { card.classList.remove('leaving'); recount(); apply(); }, 180);
}

document.querySelectorAll('.act').forEach(b => {
  b.addEventListener('click', e => { e.stopPropagation(); act(b.closest('.card'), b.dataset.act); });
});
document.querySelectorAll('.tabs button').forEach(b => {
  b.addEventListener('click', () => {
    tab = b.dataset.tab;
    document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('on', x === b));
    apply();
  });
});
document.getElementById('q').addEventListener('input', apply);
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  const active = document.querySelector('.card.rev.active') || cards().find(c => match(c));
  if (!active) return;
  if (e.key === '1') { e.preventDefault(); act(active, 'star'); }
  if (e.key === '2') { e.preventDefault(); act(active, 'kill'); }
});
recount();
apply();
"""


def render_review_page(data: dict) -> str:
    words = data["words"]
    ordered = sorted(
        words,
        key=lambda w: (1 if w.get("learned") else 0, 0 if w.get("starred") else 1,
                       w.get("first_seen") or "", w["word"].lower()),
    )
    cards_html = []
    for w in ordered:
        collocs = "".join(f"<li>{esc(c)}</li>" for c in (w.get("collocations") or []) if c)
        cards_html.append(
            f'<article class="card rev" data-word="{esc(w["word"])}" '
            f'data-learn="{1 if w.get("learned") else 0}" data-star="{1 if w.get("starred") else 0}">'
            f'<div class="acts">'
            f'<button class="act star{" on" if w.get("starred") else ""}" data-act="star">'
            f'{"★" if w.get("starred") else "☆"}</button>'
            f'<button class="act kill{" on" if w.get("learned") else ""}" data-act="kill">'
            f'{"斩了" if w.get("learned") else "斩"}</button>'
            f'</div>'
            f'<div class="revmeta">{esc((w.get("tags") or ["未分类"])[0])} · {esc(w.get("first_seen") or "")}</div>'
            f'<div class="card-head"><span class="w">{esc(w["word"])}</span>'
            + (f'<span class="ph">{esc(w["phonetic"])}</span>' if w.get("phonetic") else "")
            + (f'<span class="pos">{esc(w["pos"])}</span>' if w.get("pos") else "")
            + "</div>"
            + (f'<div class="zh">{esc(w["zh"])}</div>' if w.get("zh") else "")
            + (f'<ul class="colloc">{collocs}</ul>' if collocs else "")
            + (f'<div class="eg">{esc(w["example"])}</div>' if w.get("example") else "")
            + (f'<div class="myerr">当初为什么记它：{esc(w["note"])}</div>' if w.get("note") else "")
            + "</article>"
        )

    n_open = sum(1 for w in words if not w.get("learned"))
    n_star = sum(1 for w in words if w.get("starred"))
    n_kill = sum(1 for w in words if w.get("learned"))

    return "".join([
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>背词 · vocab-ledger</title>",
        f"<style>{CSS}{REVIEW_CSS}</style></head>",
        '<body class="review"><div class="rev-wrap">',
        '<div class="rev-head">',
        '<div><div class="kicker">vocab-ledger</div><h1>背词</h1>',
        f'<div class="meta">{len(words)} 词 · 数据写入 data/words.json</div></div>',
        '<div class="tabs">',
        f'<button data-tab="open" class="on">待背<b id="cnt-open">{n_open}</b></button>',
        f'<button data-tab="starred">收藏<b id="cnt-star">{n_star}</b></button>',
        f'<button data-tab="learned">斩了<b id="cnt-kill">{n_kill}</b></button>',
        "</div></div>",
        '<div class="rev-bar">',
        '<input id="q" class="search" type="search" placeholder="搜单词或释义…">',
        '<span class="hint">键盘 1=收藏 2=斩 · 只监听 127.0.0.1</span>',
        "</div>",
        '<div class="cards rev">',
        "".join(cards_html),
        "</div>",
        '<div class="empty-rev" id="empty" style="display:none">这个分类下没有词。</div>',
        "</div>",
        f"<script>{REVIEW_JS}</script>",
        "</body></html>",
    ])


def cmd_review(args) -> int:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    lock = threading.Lock()
    data = load()
    if DATA_FILE.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DATA_FILE, BACKUP_DIR / f"words-{datetime.now():%Y%m%d-%H%M%S}-review.json")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):  # 静音
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _guard(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def do_GET(self):
            if not self._guard():
                return self._send(403, b"forbidden", "text/plain; charset=utf-8")
            if self.path.split("?")[0] not in ("/", "/index.html"):
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            with lock:
                page = render_review_page(load())
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            if not self._guard() or self.path.split("?")[0] != "/api/mark":
                return self._send(403, b"forbidden", "text/plain; charset=utf-8")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
                word = str(payload.get("word", ""))
                field = payload.get("field")
                value = bool(payload.get("value"))
                with lock:
                    d = load()
                    w = find_word(d, word)
                    if w is None:
                        return self._send(404, b'{"ok":false}', "application/json")
                    if field == "star":
                        w["starred"] = value
                    elif field == "learn":
                        w["learned"] = value
                        w["learned_at"] = date.today().isoformat() if value else None
                    else:
                        return self._send(400, b'{"ok":false}', "application/json")
                    save(d, backup=False)
                log(f"review: {word} {field}={value}")
                return self._send(200, b'{"ok":true}', "application/json")
            except Exception as exc:
                return self._send(500, json.dumps({"ok": False, "error": str(exc)}).encode(), "application/json")

    port = args.port
    for attempt in range(10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    else:
        raise SystemExit("找不到可用端口")

    url = f"http://127.0.0.1:{port}/"
    print(f"背词页已启动：{url}")
    print("按 Ctrl+C 结束。数据直接写入 data/words.json。")
    if args.open:
        open_path(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vocab.py", description="本地生词账本：记下不会的词，每周出一张纸。")
    p.add_argument("--version", action="version", version="vocab.py 2.0")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="记一个/一批不会的词")
    a.add_argument("word", nargs="?")
    a.add_argument("--file", help="批量导入 JSON")
    a.add_argument("--zh", default="", help="中文释义")
    a.add_argument("--phonetic", default="", help="音标")
    a.add_argument("--pos", default="", help="词性")
    a.add_argument("--colloc", action="append", default=[], help="搭配，可重复")
    a.add_argument("--example", default="", help="例句")
    a.add_argument("--tag", action="append", default=[], help="主题标签，可重复")
    a.add_argument("--source", default="", help="来源说明")
    a.add_argument("--note", default="", help="当初为什么记它（错因）")
    a.add_argument("--date", default="", help="录入日期 YYYY-MM-DD")
    a.set_defaults(func=cmd_add)

    for name, func, hlp in (("learn", cmd_learn, "斩：标记背过了（别名 kill）"),
                            ("unlearn", cmd_unlearn, "放回未背"),
                            ("star", cmd_star, "收藏：留在收藏夹"),
                            ("unstar", cmd_unstar, "取消收藏")):
        s = sub.add_parser(name, help=hlp, aliases=(["kill"] if name == "learn" else []))
        s.add_argument("words", nargs="*", help="一个或多个单词")
        s.add_argument("--file", help="批量 JSON")
        s.add_argument("--date", default="", help="日期 YYYY-MM-DD，默认今天")
        s.set_defaults(func=func)

    l = sub.add_parser("list", help="列出词库")
    l.add_argument("--all", action="store_true", help="连背过的一起列")
    l.add_argument("--learned", action="store_true", help="只看背过的")
    l.add_argument("--starred", action="store_true", help="只看收藏的")
    l.add_argument("--tag")
    l.add_argument("--week", help="按录入周筛选，如 2026-W47")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    sub.add_parser("stats", help="统计概览").set_defaults(func=cmd_stats)
    sub.add_parser("weeks", help="按周统计新增").set_defaults(func=cmd_weeks)

    r = sub.add_parser("report", help="生成 HTML 周报")
    r.add_argument("--week", help="如 2026-W47，默认本周")
    r.add_argument("--out", help="输出路径")
    r.add_argument("--open", action="store_true", help="生成后用浏览器打开")
    r.add_argument("--max-review", type=int, default=60, help="清单最多列多少词")
    r.add_argument("--title", help="自定义标题")
    r.set_defaults(func=cmd_report)

    w2 = sub.add_parser("weekly", help="补漏 + 出当周报告（幂等，给调度用）")
    w2.add_argument("--open", action="store_true")
    w2.add_argument("--max-review", type=int, default=60)
    w2.add_argument("--max-weeks", type=int, default=8)
    w2.set_defaults(func=cmd_weekly)

    rv = sub.add_parser("review", help="本地背词页：卡片 + 收藏/斩按钮，直写词库")
    rv.add_argument("--port", type=int, default=8765, help="端口，默认 8765，占用则自动顺延")
    rv.add_argument("--open", action="store_true", help="启动后打开浏览器")
    rv.set_defaults(func=cmd_review)

    d = sub.add_parser("doctor", help="自检：词库、报告、计划任务是否正常")
    d.add_argument("--task", help="顺带检查一个 Windows 计划任务")
    d.set_defaults(func=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    force_utf8()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
