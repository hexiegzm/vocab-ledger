"""渲染层测试：结构完整、周过滤、空库容错、HTML 转义。"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vocab  # noqa: E402

WEEK = date(2026, 11, 16)  # 2026-W47 的周一


def build():
    data = {"words": []}
    vocab.upsert(data, {
        "word": "mitigate", "zh": "缓解", "tags": ["环境"],
        "status": "wrong", "note": "整句没译出来", "first_seen": "2026-11-17",
        "collocations": ["mitigate the impact of 减轻……的影响"],
        "example": "Planting trees can mitigate the effects of climate change.",
        "phonetic": "/ˈmɪtɪɡeɪt/", "pos": "v.",
    })
    vocab.upsert(data, {
        "word": "nuance", "zh": "细微差别", "tags": ["写作搭配"],
        "status": "new", "first_seen": "2026-11-10",  # 上一周
    })
    return data


class RenderTest(unittest.TestCase):
    def test_empty_library_renders_without_crashing(self):
        doc = vocab.render_html({"words": []}, WEEK)
        self.assertIn("<!DOCTYPE html>", doc)
        self.assertIn("下周背诵清单", doc)
        self.assertIn("本周没有错误记录", doc)

    def test_all_sections_present(self):
        doc = vocab.render_html(build(), WEEK)
        for heading in ("本周错题档案", "下周背诵清单", "本周已掌握", "遮字自测", "本周建议"):
            self.assertIn(heading, doc, f"缺少小节：{heading}")

    def test_week_filter_applies_to_new_words_and_errors(self):
        doc = vocab.render_html(build(), WEEK)
        self.assertIn("本周新增", doc)
        self.assertIn("整句没译出来", doc)  # 本周错误，应该出现在错题档案
        # nuance 是上一周加入的，不该算进本周新增，但仍在背诵清单里
        self.assertIn("nuance", doc)

    def test_word_without_any_event_still_listed_first(self):
        doc = vocab.render_html(build(), WEEK)
        self.assertIn("待巩固", doc)

    def test_grouped_by_first_tag(self):
        doc = vocab.render_html(build(), WEEK)
        self.assertIn("环境", doc)
        self.assertIn("写作搭配", doc)

    def test_self_test_table_has_one_row_per_word(self):
        data = build()
        doc = vocab.render_html(data, WEEK)
        quiz = doc.split("遮字自测", 1)[1]
        self.assertEqual(quiz.count('class="blank"'), len(data["words"]))

    def test_html_is_escaped(self):
        data = {"words": []}
        vocab.upsert(data, {
            "word": "evil", "zh": "<script>alert(1)</script>",
            "example": '<img src=x onerror=alert(1)>',
            "status": "wrong", "note": "<b>粗体</b>",
        })
        doc = vocab.render_html(data, WEEK)
        self.assertNotIn("<script>alert(1)</script>", doc)
        self.assertNotIn("onerror=alert(1)>", doc)
        self.assertIn("&lt;script&gt;", doc)

    def test_stats_numbers_are_correct(self):
        data = build()
        doc = vocab.render_html(data, WEEK)
        # 本周新增 1（mitigate），生词总量 2
        self.assertIn('<div class="n">1</div><div class="l">本周新增</div>', doc)
        self.assertIn('<div class="n">2</div><div class="l">生词总量</div>', doc)

    def test_mastered_word_leaves_the_review_list(self):
        data = {"words": []}
        w, _ = vocab.upsert(data, {"word": "solid", "zh": "扎实的", "first_seen": "2026-11-17"})
        for _ in range(4):
            vocab.apply_event(w, "correct", when=WEEK + timedelta(days=1))
        doc = vocab.render_html(data, WEEK)
        self.assertIn("本周已掌握", doc)
        self.assertIn('class="chip"', doc)
        self.assertEqual(len(vocab.render_html(data, WEEK).split("遮字自测")[1].split('class="blank"')) - 1, 0)


if __name__ == "__main__":
    unittest.main()
