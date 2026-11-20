"""掌握度与数据合并的单元测试（零依赖，标准库 unittest）。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vocab  # noqa: E402


class MasteryTest(unittest.TestCase):
    def setUp(self):
        self.data = {"words": []}
        self.w, _ = vocab.upsert(self.data, {"word": "resilient", "zh": "有韧性的"})

    def test_new_word_is_learning(self):
        self.assertEqual(self.w["status"], "learning")
        self.assertEqual(self.w["score"], 0)

    def test_two_corrects_is_reviewing_not_mastered(self):
        for _ in range(2):
            vocab.apply_event(self.w, "correct")
        self.assertEqual(self.w["status"], "reviewing")

    def test_four_corrects_becomes_mastered(self):
        for _ in range(4):
            vocab.apply_event(self.w, "correct")
        self.assertEqual(self.w["score"], 4)
        self.assertEqual(self.w["consecutive"], 4)
        self.assertEqual(self.w["status"], "mastered")

    def test_wrong_resets_streak_and_drops_score(self):
        for _ in range(4):
            vocab.apply_event(self.w, "correct")
        vocab.apply_event(self.w, "wrong")
        self.assertEqual(self.w["consecutive"], 0)
        self.assertEqual(self.w["score"], 2)
        self.assertNotEqual(self.w["status"], "mastered")

    def test_score_floor_and_ceiling(self):
        for _ in range(5):
            vocab.apply_event(self.w, "wrong")
        self.assertEqual(self.w["score"], 0)
        for _ in range(9):
            vocab.apply_event(self.w, "correct")
        self.assertEqual(self.w["score"], 5)


class UpsertTest(unittest.TestCase):
    def test_duplicate_word_merges_not_duplicates(self):
        data = {"words": []}
        vocab.upsert(data, {"word": "mitigate", "zh": "缓解"})
        vocab.upsert(data, {"word": "Mitigate", "phonetic": "/m/"})
        self.assertEqual(len(data["words"]), 1)
        self.assertEqual(data["words"][0]["phonetic"], "/m/")

    def test_merge_fills_blanks_but_keeps_existing(self):
        data = {"words": []}
        vocab.upsert(data, {"word": "nuance", "zh": "细微差别"})
        vocab.upsert(data, {"word": "nuance", "zh": "覆盖值", "example": "an example"})
        w = data["words"][0]
        self.assertEqual(w["zh"], "细微差别")
        self.assertEqual(w["example"], "an example")

    def test_import_is_idempotent(self):
        data = {"words": []}
        rec = {"word": "trade-off", "zh": "取舍", "status": "wrong", "note": "写错"}
        vocab.upsert(data, rec)
        vocab.upsert(data, rec)
        self.assertEqual(len(data["words"][0]["history"]), 1)
        self.assertEqual(data["words"][0]["score"], 0)

    def test_mark_is_not_deduplicated(self):
        data = {"words": []}
        w, _ = vocab.upsert(data, {"word": "nuance", "zh": "细微差别"})
        for _ in range(3):
            vocab.apply_event(w, "correct")  # 手动记录不需要去重
        self.assertEqual(w["score"], 3)

    def test_tags_and_collocations_are_unioned(self):
        data = {"words": []}
        vocab.upsert(data, {"word": "x", "tags": ["环境"]})
        w, _ = vocab.upsert(data, {"word": "x", "tags": ["环境", "写作"]})
        self.assertEqual(w["tags"], ["环境", "写作"])

    def test_import_event_uses_first_seen_date(self):
        """导入历史数据时，错误事件要落在 first_seen 那天，而不是今天。"""
        data = {"words": []}
        vocab.upsert(data, {
            "word": "x", "zh": "甲", "first_seen": "2026-11-10",
            "status": "wrong", "note": "写错了",
        })
        self.assertEqual(data["words"][0]["history"][0]["date"], "2026-11-10")
        self.assertEqual(data["words"][0]["last_review"], "2026-11-10")


class WeekTest(unittest.TestCase):
    def test_week_key_and_monday(self):
        from datetime import date

        d = date(2026, 11, 20)  # 星期五
        self.assertEqual(vocab.week_key(d), "2026-W47")
        self.assertEqual(vocab.week_monday(d), date(2026, 11, 16))

    def test_parse_week_accepts_both_forms(self):
        from datetime import date

        self.assertEqual(vocab.parse_week("2026-W47"), date(2026, 11, 16))
        self.assertEqual(vocab.parse_week("2026-11-20"), date(2026, 11, 16))


if __name__ == "__main__":
    unittest.main()
