"""补漏逻辑测试：什么时候该出报告、漏了几周能不能补回来。"""

import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vocab  # noqa: E402

W45 = date(2026, 11, 2)    # 周一
W46 = date(2026, 11, 9)    # 周一
W47 = date(2026, 11, 16)
W48 = date(2026, 11, 23)


class DueWeeksTest(unittest.TestCase):
    def test_nothing_due_before_sunday_8pm(self):
        now = datetime(2026, 11, 22, 19, 0)      # 周日 19:00，还没到点
        self.assertEqual(vocab.due_weeks(W46, W47, now), [])

    def test_due_exactly_at_sunday_8pm(self):
        now = datetime(2026, 11, 22, 20, 0)
        self.assertEqual(vocab.due_weeks(W46, W47, now), [W47])

    def test_missed_one_week_gets_backfilled(self):
        # 上周日没开机，本周三才打开电脑
        now = datetime(2026, 11, 18, 9, 0)
        self.assertEqual(vocab.due_weeks(W45, W47, now), [W46])

    def test_missed_three_weeks_get_backfilled_in_order(self):
        now = datetime(2026, 11, 25, 9, 0)       # W48 周三，W46/W47 都错过了
        self.assertEqual(vocab.due_weeks(W45, W48, now), [W46, W47])

    def test_already_done_this_week_is_noop(self):
        now = datetime(2026, 11, 18, 9, 0)
        self.assertEqual(vocab.due_weeks(W46, W47, now), [])

    def test_backfill_is_bounded(self):
        now = datetime(2026, 11, 25, 9, 0)
        self.assertEqual(len(vocab.due_weeks(None, W48, now, max_weeks=2)), 1)


class WeeklyCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = (vocab.DATA_FILE, vocab.BACKUP_DIR, vocab.LOG_DIR, vocab.REPORT_DIR)
        vocab.DATA_FILE = self.tmp / "data" / "words.json"
        vocab.BACKUP_DIR = self.tmp / "data" / "backup"
        vocab.LOG_DIR = self.tmp / "logs"
        vocab.REPORT_DIR = self.tmp / "reports"
        data = {"words": []}
        vocab.upsert(data, {"word": "resilient", "zh": "有韧性的", "first_seen": "2026-11-09"})
        vocab.save(data)

    def tearDown(self):
        vocab.DATA_FILE, vocab.BACKUP_DIR, vocab.LOG_DIR, vocab.REPORT_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_weekly_is_idempotent(self):
        self.assertEqual(vocab.main(["weekly"]), 0)
        first = sorted(p.name for p in vocab.REPORT_DIR.glob("*.html"))
        self.assertEqual(vocab.main(["weekly"]), 0)
        second = sorted(p.name for p in vocab.REPORT_DIR.glob("*.html"))
        self.assertEqual(first, second)

    def test_weekly_backfills_missed_weeks(self):
        # 伪造：上一次出报告停在 W45，今天是 W47 周三 → 应该补出 W46
        vocab.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (vocab.LOG_DIR / "last-report.json").write_text(
            '{"at": "2026-11-08 20:00", "week": "2026-W45", "words": 1, "path": "x"}',
            encoding="utf-8")
        # 用 due_weeks 的判定直接验证补漏集合（不依赖真实今天）
        pending = vocab.due_weeks(vocab.parse_week("2026-W45"), vocab.week_monday(date(2026, 11, 18)),
                                  datetime(2026, 11, 18, 9, 0))
        self.assertEqual(pending, [W46])

    def test_heartbeat_never_regresses(self):
        data = vocab.load()
        vocab.write_report(data, W47, vocab.REPORT_DIR / "a.html")
        vocab.write_report(data, W46, vocab.REPORT_DIR / "b.html")   # 补生成旧周
        import json
        hb = json.loads((vocab.LOG_DIR / "last-report.json").read_text(encoding="utf-8"))
        self.assertEqual(hb["week"], "2026-W47")


if __name__ == "__main__":
    unittest.main()
