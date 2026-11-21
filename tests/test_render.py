"""渲染层测试：单文件五视图、卡片、互斥显示、转义。"""

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vocab  # noqa: E402

WEEK = date(2026, 11, 16)   # 2026-W47 周一


def build():
    data = {"words": []}
    vocab.upsert(data, {
        "word": "mitigate", "zh": "缓解", "tags": ["环境"],
        "note": "「减轻」整句没译出来", "first_seen": "2026-11-17",
        "collocations": ["mitigate the impact of 减轻……的影响"],
        "example": "Planting trees can mitigate the effects of climate change.",
        "phonetic": "/ˈmɪtɪɡeɪt/", "pos": "v.",
    })
    vocab.upsert(data, {
        "word": "nuance", "zh": "细微差别", "tags": ["写作搭配"],
        "first_seen": "2026-11-10",  # 上一周
    })
    return data


class AppTest(unittest.TestCase):
    def test_five_panels_and_tabs(self):
        doc = vocab.render_html(build(), WEEK)
        for key in ("study", "t1", "t2", "fav", "kill"):
            self.assertIn(f'data-panel="{key}"', doc, f"缺少 {key} 视图")
        self.assertEqual(doc.count('<section class="panel'), 5)
        self.assertIn("背词", doc)
        self.assertIn("收藏夹", doc)
        self.assertIn("斩词", doc)

    def test_only_one_panel_visible_by_default(self):
        doc = vocab.render_html(build(), WEEK)
        self.assertEqual(doc.count('class="panel on"'), 1)

    def test_cards_not_tables(self):
        doc = vocab.render_html(build(), WEEK)
        self.assertIn('class="card"', doc)
        self.assertNotIn('<table class="grid"', doc)

    def test_week_filter_in_t1(self):
        doc = vocab.render_html(build(), WEEK)
        t1 = doc.split('id="p-t1"', 1)[1].split('id="p-t2"', 1)[0]
        self.assertIn("mitigate", t1)
        self.assertNotIn("nuance", t1)          # 上周的词不进表一

    def test_static_mode_has_badges_not_buttons(self):
        data = build()
        w = vocab.find_word(data, "mitigate")
        w["starred"] = True
        doc = vocab.render_html(data, WEEK)
        self.assertIn('class="badge star"', doc)
        self.assertNotIn('data-act="star"', doc)

    def test_interactive_mode_has_buttons(self):
        data = build()
        doc = vocab.render_app(data, WEEK, interactive=True)
        self.assertIn('data-act="star"', doc)
        self.assertIn('data-act="kill"', doc)
        self.assertIn('data-word="mitigate"', doc)
        self.assertIn("/api/mark", doc)

    def test_killed_word_leaves_library_panel(self):
        data = build()
        w = vocab.find_word(data, "nuance")
        w["learned"] = True
        w["learned_at"] = "2026-11-18"
        doc = vocab.render_html(data, WEEK)
        t2 = doc.split('id="p-t2"', 1)[1].split('id="p-fav"', 1)[0]
        self.assertNotIn("nuance", t2)
        kill = doc.split('id="p-kill"', 1)[1]
        self.assertIn("nuance", kill)

    def test_html_is_escaped(self):
        data = {"words": []}
        vocab.upsert(data, {"word": "evil", "zh": "<script>alert(1)</script>",
                            "note": "<b>x</b>"})
        for interactive in (False, True):
            doc = vocab.render_app(data, WEEK, interactive=interactive)
            self.assertNotIn("<script>alert(1)</script>", doc)

    def test_empty_library_renders(self):
        for interactive in (False, True):
            doc = vocab.render_app({"words": []}, WEEK, interactive=interactive)
            self.assertIn("<!DOCTYPE html>", doc)

    def test_week_ordinal_counts_from_first_word(self):
        data = build()
        # 最早词在 2026-11-10（W46 周二）→ 那周是第 1 周
        self.assertEqual(vocab.week_ordinal(data, date(2026, 11, 9)), 1)
        self.assertEqual(vocab.week_ordinal(data, date(2026, 11, 16)), 2)
        self.assertEqual(vocab.week_ordinal(data, date(2026, 12, 7)), 5)

    def test_title_uses_ordinal_not_iso_week(self):
        doc = vocab.render_html(build(), WEEK)   # 2026-W47，但首个词在 W46
        self.assertIn("<h1>第 2 周 · 生词报告</h1>", doc)
        self.assertNotIn("第 47 周", doc)

    def test_kind_badge_and_distribution(self):
        data = build()
        vocab.find_word(data, "mitigate")["kind"] = "拼写"
        vocab.upsert(data, {"word": "give", "zh": "给", "kind": "语法", "first_seen": "2026-11-17"})
        doc = vocab.render_html(data, WEEK)
        self.assertIn('class="kind"', doc)
        self.assertIn('class="chips dist"', doc)
        dist = doc.split('class="chips dist"', 1)[1].split("</div>", 1)[0]
        self.assertIn("拼写", dist)
        self.assertIn("语法", dist)

    def test_interactive_js_refreshes_panels(self):
        """点完必须按服务端重绘，否则斩掉的卡片还留在待背列表里。"""
        doc = vocab.render_app(build(), WEEK, interactive=True)
        self.assertIn("await refreshPanels();", doc)
        self.assertIn("fetch('/', { cache: 'no-store' })", doc)
        self.assertIn("if (name === 'study' || name === 't2') ok = !learned;", doc)
        # 按钮传 'kill'，状态字段是 data-learn：不映射就会「只能斩、不能取消」
        self.assertIn("const current = isStar ? card.dataset.star : card.dataset.learn;", doc)


if __name__ == "__main__":
    unittest.main()
