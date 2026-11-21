"""渲染层测试：四个独立页面、互链、周过滤、转义。"""

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vocab  # noqa: E402

WEEK = date(2026, 11, 16)   # 2026-W47 周一
STEM = "vocab-2026-W47"


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


def page(data, name):
    return vocab.render_page(data, WEEK, name, STEM)


class PagesTest(unittest.TestCase):
    def test_all_four_pages_render(self):
        data = build()
        for name, heading in (("main", "表一 · 本周新增"),
                              ("t2", "表二 · 不会的词库"),
                              ("fav", "收藏夹"),
                              ("kill", "斩词")):
            doc = page(data, name)
            self.assertIn("<!DOCTYPE html>", doc)
            self.assertIn(heading, doc, f"{name} 页缺少标题")

    def test_empty_library_renders_every_page(self):
        for name in ("main", "t2", "fav", "kill"):
            doc = page({"words": []}, name)
            self.assertIn("<!DOCTYPE html>", doc)

    def test_nav_links_point_to_other_pages(self):
        doc = page(build(), "main")
        for suffix in ("t2", "fav", "kill"):
            self.assertIn(f'href="{STEM}-{suffix}.html"', doc)
        self.assertIn(f'href="{STEM}.html"', doc)

    def test_current_page_is_highlighted(self):
        doc = page(build(), "t2")
        self.assertIn('href="vocab-2026-W47-t2.html" class="on"', doc)

    def test_main_page_only_lists_this_week(self):
        doc = page(build(), "main")
        self.assertIn("mitigate", doc)
        self.assertIn("「减轻」整句没译出来", doc)
        self.assertNotIn("nuance", doc)          # 上周的词不进表一

    def test_library_page_lists_all_open_words(self):
        doc = page(build(), "t2")
        self.assertIn("mitigate", doc)
        self.assertIn("nuance", doc)
        self.assertIn('id="lib"', doc)
        self.assertIn('id="q"', doc)             # 搜索框只在表二

    def test_favorites_page(self):
        data = build()
        vocab.find_word(data, "mitigate")["starred"] = True
        doc = page(data, "fav")
        self.assertIn("mitigate", doc)
        self.assertNotIn("nuance", doc)
        self.assertIn('<span class="star">★</span>', doc)

    def test_kill_page_lists_killed_words(self):
        data = build()
        w = vocab.find_word(data, "nuance")
        w["learned"] = True
        w["learned_at"] = "2026-11-18"
        doc = page(data, "kill")
        self.assertIn("nuance", doc)
        self.assertIn("本周 1 词", doc)

    def test_killed_word_leaves_the_library_page(self):
        data = build()
        w = vocab.find_word(data, "nuance")
        w["learned"] = True
        doc = page(data, "t2")
        self.assertNotIn("nuance", doc)

    def test_truncation_hint(self):
        data = {"words": []}
        for i in range(5):
            vocab.upsert(data, {"word": f"w{i}", "zh": f"词{i}", "first_seen": "2026-11-17"})
        doc = vocab.render_page(data, WEEK, "t2", STEM, max_review=2)
        self.assertIn("另有 3 个词未列入本期", doc)

    def test_html_is_escaped(self):
        data = {"words": []}
        vocab.upsert(data, {
            "word": "evil", "zh": "<script>alert(1)</script>",
            "example": '<img src=x onerror=alert(1)>',
            "note": "<b>粗体</b>",
        })
        for name in ("main", "t2"):
            self.assertNotIn("<script>alert(1)</script>", page(data, name))

    def test_render_pages_returns_four_documents(self):
        pages = vocab.render_pages(build(), WEEK, STEM)
        self.assertEqual(set(pages), {"main", "t2", "fav", "kill"})


class ReviewPageTest(unittest.TestCase):
    def test_review_page_has_cards_and_buttons(self):
        data = build()
        doc = vocab.render_review_page(data)
        self.assertEqual(doc.count('class="card rev"'), len(data["words"]))
        self.assertIn('data-act="star"', doc)
        self.assertIn('data-act="kill"', doc)
        self.assertIn('data-word="mitigate"', doc)

    def test_review_page_escapes_html(self):
        data = {"words": []}
        vocab.upsert(data, {"word": "evil", "zh": "<script>alert(1)</script>"})
        self.assertNotIn("<script>alert(1)</script>", vocab.render_review_page(data))

    def test_review_js_writes_learn_not_kill(self):
        """点击传的是 kill，但卡片状态字段必须是 learn，否则点了不重排。"""
        doc = vocab.render_review_page(build())
        self.assertIn("const key = field === 'star' ? 'star' : 'learn';", doc)
        self.assertIn("card.dataset[key] =", doc)


if __name__ == "__main__":
    unittest.main()
