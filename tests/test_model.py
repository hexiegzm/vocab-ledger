"""数据模型测试：录入、合并、状态、以及 v1 → v2 迁移。"""

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vocab  # noqa: E402


class UpsertTest(unittest.TestCase):
    def test_new_word_defaults_to_open(self):
        data = {"words": []}
        w, created = vocab.upsert(data, {"word": "astronaut", "zh": "航天员"})
        self.assertTrue(created)
        self.assertFalse(w["learned"])
        self.assertIsNone(w["learned_at"])
        self.assertEqual(w["first_seen"], date.today().isoformat())

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

    def test_note_is_kept_once_set(self):
        data = {"words": []}
        vocab.upsert(data, {"word": "x", "note": "写成 y"})
        vocab.upsert(data, {"word": "x", "note": "另一个错因"})
        self.assertEqual(data["words"][0]["note"], "写成 y")

    def test_tags_and_collocations_are_unioned(self):
        data = {"words": []}
        vocab.upsert(data, {"word": "x", "tags": ["环境"]})
        w, _ = vocab.upsert(data, {"word": "x", "tags": ["环境", "写作"]})
        self.assertEqual(w["tags"], ["环境", "写作"])


class MigrateTest(unittest.TestCase):
    def test_v1_payload_is_upgraded(self):
        v1 = {
            "version": 1,
            "words": [{
                "word": "astronaut", "zh": "航天员",
                "score": 4, "consecutive": 4, "status": "mastered",
                "last_review": "2026-11-18",
                "history": [{"date": "2026-11-20", "event": "wrong", "note": "写成 australian"}],
            }],
        }
        out = vocab.migrate(v1)
        w = out["words"][0]
        self.assertEqual(out["version"], 2)
        self.assertTrue(w["learned"])
        self.assertEqual(w["learned_at"], "2026-11-18")
        self.assertEqual(w["note"], "写成 australian")
        for dead in ("score", "consecutive", "status", "last_review", "history"):
            self.assertNotIn(dead, w)

    def test_v1_learning_word_stays_open(self):
        v1 = {"version": 1, "words": [{"word": "x", "zh": "甲", "status": "learning", "score": 0}]}
        w = vocab.migrate(v1)["words"][0]
        self.assertFalse(w["learned"])
        self.assertIsNone(w["learned_at"])

    def test_migrate_is_idempotent(self):
        v1 = {"version": 1, "words": [{"word": "x", "status": "learning"}]}
        once = vocab.migrate(v1)
        twice = vocab.migrate(once)
        self.assertEqual(once["words"][0]["learned"], twice["words"][0]["learned"])


class WeekTest(unittest.TestCase):
    def test_week_key_and_monday(self):
        d = date(2026, 11, 20)  # 星期五
        self.assertEqual(vocab.week_key(d), "2026-W47")
        self.assertEqual(vocab.week_monday(d), date(2026, 11, 16))

    def test_parse_week_accepts_both_forms(self):
        self.assertEqual(vocab.parse_week("2026-W47"), date(2026, 11, 16))
        self.assertEqual(vocab.parse_week("2026-11-20"), date(2026, 11, 16))


if __name__ == "__main__":
    unittest.main()
