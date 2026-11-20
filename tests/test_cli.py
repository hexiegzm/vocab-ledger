"""命令行端到端测试：add → mark → list → report 全流程跑通。"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vocab  # noqa: E402


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = (vocab.DATA_FILE, vocab.BACKUP_DIR, vocab.LOG_DIR, vocab.REPORT_DIR)
        vocab.DATA_FILE = self.tmp / "data" / "words.json"
        vocab.BACKUP_DIR = self.tmp / "data" / "backup"
        vocab.LOG_DIR = self.tmp / "logs"
        vocab.REPORT_DIR = self.tmp / "reports"

        self.batch = self.tmp / "seed.json"
        self.batch.write_text(json.dumps({"words": [
            {"word": "resilient", "zh": "有韧性的", "status": "wrong", "note": "写成 resistent"},
            {"word": "mitigate", "zh": "缓解"},
        ]}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        vocab.DATA_FILE, vocab.BACKUP_DIR, vocab.LOG_DIR, vocab.REPORT_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_file_then_mark(self):
        self.assertEqual(vocab.main(["add", "--file", str(self.batch)]), 0)
        self.assertEqual(vocab.main(["mark", "mitigate", "correct", "--note", "听写对"]), 0)
        data = vocab.load()
        by_word = {w["word"]: w for w in data["words"]}
        self.assertEqual(len(by_word), 2)
        self.assertEqual(by_word["mitigate"]["status"], "reviewing")
        self.assertEqual(by_word["resilient"]["status"], "learning")

    def test_mark_unknown_word_exits_2(self):
        vocab.main(["add", "--file", str(self.batch)])
        self.assertEqual(vocab.main(["mark", "nonexistent", "correct"]), 2)

    def test_report_writes_file(self):
        vocab.main(["add", "--file", str(self.batch)])
        self.assertEqual(vocab.main(["report"]), 0)
        files = list(vocab.REPORT_DIR.glob("vocab-*.html"))
        self.assertEqual(len(files), 1)
        self.assertIn("错题档案", files[0].read_text(encoding="utf-8"))

    def test_stats_and_list_do_not_crash(self):
        vocab.main(["add", "--file", str(self.batch)])
        self.assertEqual(vocab.main(["stats"]), 0)
        self.assertEqual(vocab.main(["list"]), 0)
        self.assertEqual(vocab.main(["list", "--status", "learning"]), 0)
        self.assertEqual(vocab.main(["weeks"]), 0)

    def test_report_writes_heartbeat(self):
        vocab.main(["add", "--file", str(self.batch)])
        vocab.main(["report"])
        heartbeat = vocab.LOG_DIR / "last-report.json"
        self.assertTrue(heartbeat.exists())
        info = json.loads(heartbeat.read_text(encoding="utf-8"))
        self.assertEqual(info["words"], 2)
        self.assertRegex(info["week"], r"^\d{4}-W\d{2}$")
        self.assertIn("at", info)

    def test_doctor_never_crashes(self):
        self.assertIn(vocab.main(["doctor"]), (0, 1))
        vocab.main(["add", "--file", str(self.batch)])
        vocab.main(["report"])
        self.assertEqual(vocab.main(["doctor"]), 0)


if __name__ == "__main__":
    unittest.main()
