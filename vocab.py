#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vocab-ledger · 本地生词账本
===========================

纯标准库、零依赖、零联网：记录生词 → 记录答题结果 → 算掌握度 → 生成可打印的 HTML 周报。

设计要点（详见 SPEC.md）：
  * 只存「对错事件」，掌握状态由事件推导，随时可改规则重算；
  * 数据是单个可读的 JSON 文件，能手改、能比 diff、能进 git；
  * 渲染是纯函数，同样数据永远渲染出同样的报告；
  * 调度不在脚本里，用系统的 cron / 计划任务去调 `report`。

目录::

    vocab-ledger/
      vocab.py              本脚本（唯一必需文件）
      data/words.json       生词库（唯一数据源，可直接手改）
      data/backup/          每次写入前的自动备份
      reports/              生成好的 HTML 周报
      logs/                 运行日志

常用命令::

    # 批量导入（推荐，Agent 用这个）
    python vocab.py add --file examples/words.sample.json

    # 单个添加
    python vocab.py add astronaut --zh 航天员 --pos n. --phonetic "/ˈæstrənɔːt/" \
        --colloc "taikonaut 中国航天员" --tag 航天 --source "2026-W47 翻译练习" \
        --example "Yang Liwei became the first Chinese astronaut." \
        --wrong --note "写成 australian"

    # 记录一次答题结果
    python vocab.py mark astronaut correct --note "听写全对"
    python vocab.py mark astronaut wrong   --note "又写成 australian"
    python vocab.py mark --file seed/results.json

    # 查看
    python vocab.py list --status learning
    python vocab.py list --week 2026-W47
    python vocab.py stats
    python vocab.py weeks

    # 生成周报
    python vocab.py report                      # 本周
    python vocab.py report --week 2026-W47 --open
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

MAX_BACKUPS = 40
MASTER_MIN_SCORE = 4        # 掌握：score >= 4
MASTER_MIN_STREAK = 2       # 且连续答对 >= 2 次

STATUS_ORDER = {"learning": 0, "reviewing": 1, "mastered": 2}
STATUS_ZH = {"learning": "待巩固", "reviewing": "复习中", "mastered": "已掌握"}
BAD_EVENTS = ("wrong", "misspelled")


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
    """显示宽度（中文算 2）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - dw(s))


def cut(s: str, width: int) -> str:
    """按显示宽度截断（超出补省略号）。"""
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
    """接受 '2026-W47' 或 '2026-11-20'，返回该周周一。"""
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


# ---------------------------------------------------------------- 数据读写

def recompute(w: dict) -> dict:
    score = int(w.get("score", 0))
    streak = int(w.get("consecutive", 0))
    if score >= MASTER_MIN_SCORE and streak >= MASTER_MIN_STREAK:
        w["status"] = "mastered"
    elif score >= 1:
        w["status"] = "reviewing"
    else:
        w["status"] = "learning"
    return w


def load() -> dict:
    if not DATA_FILE.exists():
        return {"version": 1, "updated_at": None, "words": []}
    with open(DATA_FILE, encoding="utf-8-sig") as f:
        data = json.load(f)
    data.setdefault("words", [])
    for w in data["words"]:
        w.setdefault("history", [])
        w.setdefault("collocations", [])
        w.setdefault("tags", [])
        w.setdefault("score", 0)
        w.setdefault("consecutive", 0)
        recompute(w)
    return data


def save(data: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now_iso()
    if DATA_FILE.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(DATA_FILE, BACKUP_DIR / f"words-{stamp}.json")
        olds = sorted(BACKUP_DIR.glob("words-*.json"))
        for p in olds[:-MAX_BACKUPS]:
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


def apply_event(w: dict, result: str, note: str = "", when: date | None = None,
                dedupe: bool = False) -> bool:
    """记录一次答题事件。

    dedupe=True 时，同一天、同结果、同备注的重复事件会被忽略——批量导入用，
    保证重复导入同一份 seed 不会把错误重复记一次。
    """
    when = when or date.today()
    event = {"date": when.isoformat(), "event": result, "note": note}
    if dedupe and event in w.get("history", []):
        return False
    w.setdefault("history", []).append(event)
    if result == "correct":
        w["score"] = min(5, int(w.get("score", 0)) + 1)
        w["consecutive"] = int(w.get("consecutive", 0)) + 1
    else:
        w["score"] = max(0, int(w.get("score", 0)) - 2)
        w["consecutive"] = 0
    w["last_review"] = when.isoformat()
    recompute(w)
    return True


def upsert(data: dict, rec: dict) -> tuple[dict, bool]:
    """新增或合并一条生词。返回 (记录, 是否新建)。"""
    word = (rec.get("word") or "").strip()
    if not word:
        raise SystemExit("生词记录缺少 word 字段")
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
            "score": 0,
            "consecutive": 0,
            "status": "learning",
            "last_review": None,
            "history": [],
        }
        data["words"].append(existing)

    w = existing
    # 只补空缺，不覆盖已有内容
    for field in ("phonetic", "pos", "zh", "example"):
        if not w.get(field) and rec.get(field):
            w[field] = rec[field]
    if rec.get("source") and rec["source"] not in (w.get("source") or ""):
        w["source"] = (w.get("source") + "；" + rec["source"]).strip("；") if w.get("source") else rec["source"]
    for lst in ("collocations", "tags"):
        incoming = rec.get(lst) or []
        if isinstance(incoming, str):
            incoming = [incoming]
        for item in incoming:
            item = str(item).strip()
            if item and item not in w[lst]:
                w[lst].append(item)

    status = (rec.get("status") or "new").lower()
    # 导入历史数据时，事件应落在它真正发生的那天（first_seen），而不是今天
    when = parse_date(rec.get("first_seen")) if rec.get("first_seen") else date.today()
    if status in ("wrong", "misspelled", "error"):
        apply_event(w, "wrong", rec.get("note", ""), when, dedupe=True)
    elif status == "correct":
        apply_event(w, "correct", rec.get("note", ""), when, dedupe=True)
    recompute(w)
    return w, created


# ---------------------------------------------------------------- 命令：add

def cmd_add(args) -> int:
    data = load()
    records: list[dict] = []

    if args.file:
        path = Path(args.file)
        if not path.exists():
            raise SystemExit(f"找不到文件：{path}")
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
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
            "status": "wrong" if args.wrong else ("correct" if args.correct else "new"),
            "note": args.note,
            "first_seen": args.date,
        })
    if not records:
        raise SystemExit("没有要添加的内容：用 --file 或直接给单词。")

    new_count = merge_count = 0
    for rec in records:
        _, created = upsert(data, rec)
        new_count += created
        merge_count += (not created)

    save(data)
    log(f"add: new={new_count} merged={merge_count} src={args.file or args.word}")
    print(f"✅ 新增 {new_count} 个，合并更新 {merge_count} 个；生词库共 {len(data['words'])} 个。")
    return 0


# ---------------------------------------------------------------- 命令：mark

def cmd_mark(args) -> int:
    data = load()
    jobs: list[dict] = []
    if args.file:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            payload = payload.get("results", [])
        jobs.extend(payload)
    if args.word:
        if not args.result:
            raise SystemExit("用法：mark <单词> correct|wrong [--note ...]")
        jobs.append({"word": args.word, "result": args.result, "note": args.note})
    if not jobs:
        raise SystemExit("没有要记录的结果。")

    when = parse_date(args.date)
    done, missing = [], []
    for job in jobs:
        w = find_word(data, job.get("word", ""))
        result = str(job.get("result", "")).lower()
        if result not in ("correct", "wrong", "misspelled", "error"):
            raise SystemExit(f"result 只能是 correct / wrong，收到：{result}")
        result = "correct" if result == "correct" else "wrong"
        if w is None:
            missing.append(job.get("word", ""))
            continue
        apply_event(w, result, job.get("note", ""), when)
        done.append((w["word"], result, w["status"]))

    if done:
        save(data)
        log(f"mark: {done}")
    for word, result, status in done:
        icon = "✅" if result == "correct" else "❌"
        print(f"{icon} {pad(word, 18)} → {STATUS_ZH[status]} (score={next(w['score'] for w in data['words'] if w['word'] == word)})")
    if missing:
        print(f"⚠️  库里没有这些词，先 add：{', '.join(missing)}", file=sys.stderr)
        return 2
    return 0


# ---------------------------------------------------------------- 命令：list

def cmd_list(args) -> int:
    data = load()
    words = data["words"]
    if args.status:
        words = [w for w in words if w["status"] == args.status]
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
        print("（没有符合条件的生词）")
        return 0

    words = sorted(words, key=lambda w: (STATUS_ORDER[w["status"]], w["word"].lower()))
    print(f"{pad('单词', 20)}{pad('状态', 8)}{pad('分数', 6)}{pad('释义', 24)}标签")
    print("-" * 82)
    for w in words:
        print(
            f"{pad(w['word'], 20)}"
            f"{pad(STATUS_ZH[w['status']], 8)}"
            f"{pad(str(w.get('score', 0)), 6)}"
            f"{pad(cut(w.get('zh') or '—', 26), 28)}"
            f"{'/'.join(w.get('tags') or []) or '—'}"
        )
    print("-" * 82)
    print(f"共 {len(words)} 个")
    return 0


# ---------------------------------------------------------------- 命令：stats / weeks

def cmd_stats(args) -> int:
    data = load()
    words = data["words"]
    counts = {s: sum(1 for w in words if w["status"] == s) for s in STATUS_ORDER}
    mon = week_monday(date.today())
    sun = mon + timedelta(days=6)
    new_this = [w for w in words if in_range(w.get("first_seen"), mon, sun)]
    ok = bad = 0
    for w in words:
        for h in w.get("history", []):
            if in_range(h.get("date"), mon, sun):
                if h["event"] == "correct":
                    ok += 1
                else:
                    bad += 1
    acc = (ok / (ok + bad) * 100) if (ok + bad) else None
    print(f"生词总量     {len(words)}")
    print(f"待巩固       {counts['learning']}")
    print(f"复习中       {counts['reviewing']}")
    print(f"已掌握       {counts['mastered']}")
    print(f"本周新增     {len(new_this)}   （{week_key(mon)} {mon}~{sun}）")
    print(f"本周答题     {ok + bad} 次，正确率 " + (f"{acc:.0f}%" if acc is not None else "—"))
    return 0


def cmd_weeks(args) -> int:
    data = load()
    buckets: dict[str, list[dict]] = {}
    for w in data["words"]:
        buckets.setdefault(week_key(parse_date(w.get("first_seen"))), []).append(w)
    for wk in sorted(buckets):
        items = buckets[wk]
        mon = parse_week(wk)
        print(f"{wk}  {mon} ~ {mon + timedelta(days=6)}   新增 {len(items):3d} 个")
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
.stats{display:grid; grid-template-columns:repeat(5,1fr); gap:10px; margin:0 0 30px}
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
.badge{margin-left:auto; font-size:10.5px; padding:1px 8px; border-radius:99px; white-space:nowrap}
.badge.learning{background:var(--warn-soft); color:var(--warn)}
.badge.reviewing{background:var(--amber-soft); color:var(--amber)}
.badge.mastered{background:var(--accent-soft); color:var(--accent)}
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


def badge(status: str) -> str:
    return f'<span class="badge {status}">{STATUS_ZH[status]}</span>'


def render_card(w: dict) -> str:
    out = ['<article class="card">', '<div class="card-head">']
    out.append(f'<span class="w">{esc(w["word"])}</span>')
    if w.get("phonetic"):
        out.append(f'<span class="ph">{esc(w["phonetic"])}</span>')
    if w.get("pos"):
        out.append(f'<span class="pos">{esc(w["pos"])}</span>')
    out.append(badge(w["status"]))
    out.append("</div>")
    if w.get("zh"):
        out.append(f'<div class="zh">{esc(w["zh"])}</div>')
    collocs = [c for c in (w.get("collocations") or []) if c]
    if collocs:
        out.append('<ul class="colloc">')
        out.extend(f"<li>{esc(c)}</li>" for c in collocs)
        out.append("</ul>")
    if w.get("example"):
        out.append(f'<div class="eg">{esc(w["example"])}</div>')
    notes = [h.get("note") or "" for h in w.get("history", []) if h["event"] in BAD_EVENTS]
    if notes:
        out.append(f'<div class="myerr">我曾写错：{esc("；".join(notes[-2:]))}</div>')
    out.append("</article>")
    return "".join(out)


def render_html(data: dict, monday: date, max_review: int = 60, title: str | None = None) -> str:
    sunday = monday + timedelta(days=6)
    wk = week_key(monday)
    wnum = wk.split("-W")[1]
    words = data["words"]

    week_words = sorted(
        [w for w in words if in_range(w.get("first_seen"), monday, sunday)],
        key=lambda w: w["word"].lower(),
    )
    pending = [w for w in words if w["status"] != "mastered"]
    pending.sort(key=lambda w: (STATUS_ORDER[w["status"]], w.get("score", 0), w.get("first_seen", "")))
    mastered_week = [w for w in words if w["status"] == "mastered" and in_range(w.get("last_review"), monday, sunday)]

    errors, n_ok, n_bad = [], 0, 0
    for w in words:
        for h in w.get("history", []):
            if in_range(h.get("date"), monday, sunday):
                if h["event"] == "correct":
                    n_ok += 1
                else:
                    n_bad += 1
                    errors.append((w, h))
    errors.sort(key=lambda t: (t[1]["date"], t[0]["word"].lower()), reverse=True)
    accuracy = (n_ok / (n_ok + n_bad) * 100) if (n_ok + n_bad) else None

    shown = pending[:max_review]
    groups: dict[str, list[dict]] = {}
    for w in shown:
        tag = (w.get("tags") or ["未分类"])[0]
        groups.setdefault(tag, []).append(w)
    ordered_groups = sorted(groups.items(), key=lambda kv: (kv[0] == "未分类", -len(kv[1]), kv[0]))

    h = []
    h.append("<!DOCTYPE html>")
    h.append('<html lang="zh-CN"><head><meta charset="utf-8">')
    h.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    h.append(f"<title>{esc(title or f'第 {int(wnum)} 周 生词报告')}</title>")
    h.append(f"<style>{CSS}</style></head><body>")
    h.append('<div class="toolbar no-print"><button onclick="window.print()">打印 / 存为 PDF</button></div>')
    h.append('<div class="page">')

    # header
    h.append("<header>")
    h.append('<div class="kicker">English Vocabulary · Weekly</div>')
    h.append(f"<h1>第 {int(wnum)} 周 · 生词报告</h1>")
    h.append(f'<div class="meta">{monday.isoformat()} — {sunday.isoformat()} · {wk} · 生成于 {now_iso()}</div>')
    h.append("</header>")

    # stats
    acc_txt = f"{accuracy:.0f}%" if accuracy is not None else "—"
    h.append('<div class="stats">')
    for num, label in (
        (len(week_words), "本周新增"),
        (len(pending), "待巩固"),
        (len(mastered_week), "本周掌握"),
        (len(words), "生词总量"),
        (acc_txt, "本周正确率"),
    ):
        h.append(f'<div class="stat"><div class="n">{num}</div><div class="l">{label}</div></div>')
    h.append("</div>")

    # 1. 错题档案
    h.append(f'<h2>一、本周错题档案 <span class="count">{len(errors)} 条</span></h2>')
    if errors:
        h.append('<table><thead><tr><th style="width:20%">单词</th><th style="width:24%">正确释义</th>'
                 '<th style="width:38%">我写错的 / 漏掉的</th><th style="width:18%">日期</th></tr></thead><tbody>')
        for w, ev in errors:
            h.append(
                f'<tr><td class="w">{esc(w["word"])}</td>'
                f'<td>{esc(w.get("zh") or "—")}</td>'
                f'<td class="err-bad">{esc(ev.get("note") or "—")}</td>'
                f'<td>{esc(ev.get("date"))}</td></tr>'
            )
        h.append("</tbody></table>")
    else:
        h.append('<div class="empty">本周没有错误记录，保持住。</div>')

    # 2. 背诵清单
    if len(pending) > len(shown):
        count_txt = f"{len(shown)} 词 / 待巩固共 {len(pending)} 词"
    else:
        count_txt = f"{len(shown)} 词"
    h.append(f'<h2>二、下周背诵清单 <span class="count">{count_txt}</span></h2>')
    h.append('<p class="hint">按主题归类。先通读一遍，再翻到最后做遮字自测；错题档案里出现过的词优先背。</p>')
    if ordered_groups:
        for tag, items in ordered_groups:
            h.append('<div class="group">')
            h.append(f'<div class="group-head"><span class="t">{esc(tag)}</span><span class="c">{len(items)} 词</span></div>')
            h.append('<div class="cards">')
            h.extend(render_card(w) for w in items)
            h.append("</div></div>")
    else:
        h.append('<div class="empty">暂时没有待背的生词。</div>')
    if len(pending) > len(shown):
        h.append(f'<p class="hint">另有 {len(pending) - len(shown)} 个词未列入本期，可用 '
                 f'<code>python vocab.py list --status learning</code> 查看。</p>')

    # 3. 已掌握
    h.append(f'<h2>三、本周已掌握 <span class="count">{len(mastered_week)} 词</span></h2>')
    if mastered_week:
        h.append('<div class="chips">')
        for w in sorted(mastered_week, key=lambda x: x["word"].lower()):
            h.append(f'<span class="chip"><span class="s">{esc(w["word"])}</span> {esc(w.get("zh") or "")}</span>')
        h.append("</div>")
    else:
        h.append('<div class="empty">还没有词达到「已掌握」（连续答对 ≥ 2 次、分数 ≥ 4）。</div>')

    # 4. 遮字自测
    h.append('<h2 class="page-break">四、遮字自测 <span class="count">盖上右列，逐词默写</span></h2>')
    h.append('<p class="hint">看音标和单词，先在心里（或纸上）说中文和搭配，全部写完再翻回上一页对卡片；对完用 <code>vocab.py mark</code> 记录对错。</p>')
    if shown:
        h.append('<table class="quiz"><thead><tr><th style="width:26%">单词</th><th style="width:20%">音标</th>'
                 '<th style="width:54%">释义 / 搭配（自己写）</th></tr></thead><tbody>')
        for w in shown:
            h.append(
                f'<tr><td class="w">{esc(w["word"])}</td>'
                f'<td class="ph">{esc(w.get("phonetic") or "")}</td>'
                f'<td class="blank"></td></tr>'
            )
        h.append("</tbody></table>")
    else:
        h.append('<div class="empty">—</div>')

    # 建议
    tips = []
    if week_words:
        tips.append(f"本周新增 {len(week_words)} 个词：先看卡片建立印象，再遮字自测，最后用 mark 记录结果。")
    if n_bad:
        tips.append(f"本周有 {n_bad} 次写错/漏译记录——错题档案里的词优先，它们是真正没掌握的。")
    if accuracy is not None and accuracy < 0.6:
        tips.append("正确率偏低：把自测拆成每天 10 个词，比一次背 30 个有效。")
    if pending:
        oldest = min(pending, key=lambda w: w.get("first_seen") or "9999")
        tips.append(f"最早未掌握的词是 {oldest['word']}（{oldest.get('first_seen')} 加入），别让它跨周太多次。")
    if tips:
        h.append(f'<h2>五、本周建议</h2><ul class="tips">')
        h.extend(f"<li>{esc(t)}</li>" for t in tips)
        h.append("</ul>")

    h.append(f'<footer>由 <code>vocab.py</code> 生成 · 数据源 data/words.json · '
             f'共 {len(words)} 词 · 下期自动生成于每周日 20:00</footer>')
    h.append("</div></body></html>")
    return "\n".join(h)


def write_report(data: dict, monday: date, out: Path, max_review: int = 60,
                 title: str | None = None) -> Path:
    """渲染并落盘一份周报，同时更新心跳（心跳只会前进，不会被旧周覆盖）。"""
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
        return out  # 补生成旧周报时，不让心跳倒退
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.write_text(json.dumps({
        "at": now_iso(),
        "week": week_key(monday),
        "words": len(data["words"]),
        "path": str(out),
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


def last_reported_week() -> date | None:
    """从心跳和已生成的报告文件名里取「覆盖到的最晚一周」。"""
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
    """列出「已经过了出报告时间、但还没出」的周（按周一表示，升序）。

    规则：某一周的周报，在**该周周日 20:00 之后**就该有了。
    这样无论是准点跑、晚开机补跑，还是隔了三周才开一次机，都能算出该补哪几周。
    """
    if last is None:
        start = current - timedelta(weeks=max_weeks - 1)
    else:
        start = last + timedelta(weeks=1)
    floor = current - timedelta(weeks=max_weeks - 1)
    if start < floor:
        start = floor
    result: list[date] = []
    monday = start
    while monday <= current:
        due_at = datetime.combine(monday + timedelta(days=6), time(20, 0))
        if now >= due_at:
            result.append(monday)
        monday += timedelta(weeks=1)
    return result


def cmd_weekly(args) -> int:
    """补漏 + 出本周报告。幂等：没有到期的周就什么都不做。

    这是给系统调度用的入口——周日 20:00 触发一次，登录时也触发一次。
    后者保证「周日没开机」的情况会在下次登录时自动补上，不依赖 StartWhenAvailable。
    """
    data = load()
    now = datetime.now()
    current = week_monday(now.date())
    pending = due_weeks(last_reported_week(), current, now, args.max_weeks)
    # 不补「账本里还没有任何词」的那些周：那种周报除了空表什么都没有
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


def open_path(path: Path) -> None:
    """跨平台用系统默认程序打开文件。"""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:  # pragma: no cover
        print(f"（自动打开失败：{exc}，请手动打开 {path}）", file=sys.stderr)


# ---------------------------------------------------------------- 命令：doctor

def task_info(name: str) -> dict:
    """查询 Windows 计划任务的状态。非 Windows 或查不到时返回 {'error': ...}。"""
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
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        )
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
        add("warn", "数据文件", f"{DATA_FILE} 不存在（还没有生词）")

    try:
        data = load()
        words = data["words"]
        cnt = {s: sum(1 for w in words if w["status"] == s) for s in STATUS_ORDER}
        add("ok", "词库", f"{len(words)} 个词（待巩固 {cnt['learning']} / 复习中 {cnt['reviewing']} / 已掌握 {cnt['mastered']}）")
        broken = [w for w in words if not w.get("word") or not w.get("zh")]
        if broken:
            add("warn", "字段完整性", f"{len(broken)} 个词缺拼写或释义")
    except Exception as exc:
        add("bad", "读取账本", f"失败：{exc}")

    heartbeat = LOG_DIR / "last-report.json"
    reports = sorted(REPORT_DIR.glob("vocab-*.html")) if REPORT_DIR.exists() else []
    if heartbeat.exists():
        try:
            info = json.loads(heartbeat.read_text(encoding="utf-8"))
            when = datetime.strptime(info["at"], "%Y-%m-%d %H:%M")
            age = (datetime.now() - when).days
            add("ok" if age <= 8 else "warn", "最近出报告",
                f"{info['at']}（{info.get('week')}，{info.get('words')} 词，{age} 天前）")
        except Exception:
            add("warn", "最近出报告", "心跳文件存在但无法解析")
    elif reports:
        add("warn", "最近出报告", f"{reports[-1].name}（本次部署前生成，无心跳记录）")
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
            if code == 267011:
                add("warn", "计划任务", "这个任务还一次都没跑过，可手动触发验证")

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


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vocab.py", description="英语生词本：记录、追踪、出周报。")
    p.add_argument("--version", action="version", version="vocab.py 1.0")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="添加/合并生词")
    a.add_argument("word", nargs="?", help="单词（批量导入时可省略）")
    a.add_argument("--file", help="批量导入 JSON")
    a.add_argument("--zh", default="", help="中文释义")
    a.add_argument("--phonetic", default="", help="音标")
    a.add_argument("--pos", default="", help="词性，如 n. / v. / adj.")
    a.add_argument("--colloc", action="append", default=[], help="搭配，可重复")
    a.add_argument("--example", default="", help="例句")
    a.add_argument("--tag", action="append", default=[], help="主题标签，可重复")
    a.add_argument("--source", default="", help="来源说明")
    a.add_argument("--wrong", action="store_true", help="首次记录即记为错误")
    a.add_argument("--correct", action="store_true", help="首次记录即记为正确")
    a.add_argument("--note", default="", help="备注/错因")
    a.add_argument("--date", default="", help="first_seen 日期 YYYY-MM-DD")
    a.set_defaults(func=cmd_add)

    m = sub.add_parser("mark", help="记录一次答题结果")
    m.add_argument("word", nargs="?")
    m.add_argument("result", nargs="?", choices=["correct", "wrong"])
    m.add_argument("--file", help="批量结果 JSON")
    m.add_argument("--note", default="", help="错因说明")
    m.add_argument("--date", default="", help="日期 YYYY-MM-DD，默认今天")
    m.set_defaults(func=cmd_mark)

    l = sub.add_parser("list", help="列出生词")
    l.add_argument("--status", choices=list(STATUS_ORDER))
    l.add_argument("--tag")
    l.add_argument("--week", help="如 2026-W47，按加入周筛选")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("stats", help="统计概览")
    s.set_defaults(func=cmd_stats)

    w = sub.add_parser("weeks", help="按周统计新增")
    w.set_defaults(func=cmd_weeks)

    r = sub.add_parser("report", help="生成 HTML 周报")
    r.add_argument("--week", help="如 2026-W47，默认本周")
    r.add_argument("--out", help="输出路径")
    r.add_argument("--open", action="store_true", help="生成后用浏览器打开")
    r.add_argument("--max-review", type=int, default=60, help="背诵清单最多列多少词")
    r.add_argument("--title", help="自定义标题")
    r.set_defaults(func=cmd_report)

    d = sub.add_parser("doctor", help="自检：账本、报告、计划任务是否正常")
    d.add_argument("--task", help="顺带检查一个 Windows 计划任务，如 EnglishVocabWeekly")
    d.set_defaults(func=cmd_doctor)

    w2 = sub.add_parser("weekly", help="补漏 + 出当周报告（幂等，给调度用）")
    w2.add_argument("--open", action="store_true", help="生成后用浏览器打开最新一份")
    w2.add_argument("--max-review", type=int, default=60, help="背诵清单最多列多少词")
    w2.add_argument("--max-weeks", type=int, default=8, help="最多往回补几周")
    w2.set_defaults(func=cmd_weekly)

    return p


def main(argv: list[str] | None = None) -> int:
    force_utf8()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
