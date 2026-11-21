"""渲染层测试：结构完整、周过滤、已背词不再出现、HTML 转义。"""

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vocab  # noqa: E402

WEEK = date(2026, 11, 16)  # 2026-W47 周一


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


class RenderTest(unittest.TestCase):
    def test_empty_library_renders_without_crashing(self):
        doc = vocab.render_html({"words": []}, WEEK)
        self.assertIn("<!DOCTYPE html>", doc)
        self.assertIn("表二 · 不会的词库", doc)
        self.assertIn("这周没有新记的词", doc)

    def test_all_sections_present(self):
        doc = vocab.render_html(build(), WEEK)
        for heading in ("表一 · 本周新增", "表二 · 不会的词库", "收藏夹", "斩词"):
            self.assertIn(heading, doc, f"缺少小节：{heading}")

    def test_week_filter_applies_to_new_words(self):
        doc = vocab.render_html(build(), WEEK)
        self.assertIn("本周新增", doc)
        self.assertIn("「减轻」整句没译出来", doc)
        self.assertIn("nuance", doc)  # 上周记的，仍在清单里

    def test_note_is_shown_in_table(self):
        doc = vocab.render_html(build(), WEEK)
        self.assertIn("为什么记它", doc)
        self.assertIn("「减轻」整句没译出来", doc)

    def test_learned_word_leaves_the_list_and_quiz(self):
        data = build()
        w = vocab.find_word(data, "mitigate")
        w["learned"] = True
        w["learned_at"] = "2026-11-18"
        doc = vocab.render_html(data, WEEK)
        list_section = doc.split("id=\"t2\"", 1)[1].split("id=\"fav\"", 1)[0]
        self.assertNotIn("mitigate", list_section)      # 不再出现在待背清单
        self.assertIn("斩词", doc)
        self.assertIn('<span class="s">mitigate</span>', doc)  # 出现在"斩词"里

    def test_sections_are_anchored_and_linked(self):
        doc = vocab.render_html(build(), WEEK)
        for anchor in ("id=\"t1\"", "id=\"t2\"", "id=\"fav\"", "id=\"kill\""):
            self.assertIn(anchor, doc)
        for href in ('href="#t1"', 'href="#t2"', 'href="#fav"', 'href="#kill"'):
            self.assertIn(href, doc)
        self.assertNotIn("遮字自测", doc)
        self.assertNotIn("本周建议", doc)

    def test_html_is_escaped(self):
        data = {"words": []}
        vocab.upsert(data, {
            "word": "evil", "zh": "<script>alert(1)</script>",
            "example": '<img src=x onerror=alert(1)>',
            "note": "<b>粗体</b>",
        })
        doc = vocab.render_html(data, WEEK)
        self.assertNotIn("<script>alert(1)</script>", doc)
        self.assertIn("&lt;script&gt;", doc)

    def test_review_page_has_cards_and_buttons(self):
        data = build()
        doc = vocab.render_review_page(data)
        self.assertEqual(doc.count('class="card rev"'), len(data["words"]))
        self.assertIn('data-act="star"', doc)
        self.assertIn('data-act="kill"', doc)
        self.assertIn('data-word="mitigate"', doc)
        self.assertIn("只监听 127.0.0.1", doc)

    def test_review_page_escapes_html(self):
        data = {"words": []}
        vocab.upsert(data, {"word": "evil", "zh": "<script>alert(1)</script>"})
        doc = vocab.render_review_page(data)
        self.assertNotIn("<script>alert(1)</script>", doc)

    def test_stats_numbers(self):
        doc = vocab.render_html(build(), WEEK)
        self.assertIn('<div class="n">1</div><div class="l">表一 · 本周新增</div>', doc)
        self.assertIn('<div class="n">2</div><div class="l">表二 · 不会的词库</div>', doc)

    def test_starred_word_stays_in_favorites_after_kill(self):
        data = build()
        w = vocab.find_word(data, "mitigate")
        w["starred"] = True
        w["learned"] = True
        w["learned_at"] = "2026-11-18"
        doc = vocab.render_html(data, WEEK)
        fav = doc.split("id=\"fav\"", 1)[1].split("id=\"kill\"", 1)[0]
        self.assertIn("mitigate", fav)
        self.assertIn("斩了", fav)
        self.assertIn('<span class="star">★</span>', doc)

    def test_truncation_hint_when_over_limit(self):
        data = {"words": []}
        for i in range(5):
            vocab.upsert(data, {"word": f"w{i}", "zh": f"词{i}", "first_seen": "2026-11-17"})
        doc = vocab.render_html(data, WEEK, max_review=2)
        self.assertIn("另有 3 个词未列入本期", doc)


if __name__ == "__main__":
    unittest.main()
