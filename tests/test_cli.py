"""命令行端到端测试：add → learn → list → report 全流程跑通。"""

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
            {"word": "resilient", "zh": "有韧性的", "note": "写成 resistent"},
            {"word": "mitigate", "zh": "缓解"},
        ]}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        vocab.DATA_FILE, vocab.BACKUP_DIR, vocab.LOG_DIR, vocab.REPORT_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_file_then_learn(self):
        self.assertEqual(vocab.main(["add", "--file", str(self.batch)]), 0)
        self.assertEqual(vocab.main(["learn", "mitigate"]), 0)
        data = vocab.load()
        by_word = {w["word"]: w for w in data["words"]}
        self.assertEqual(len(by_word), 2)
        self.assertTrue(by_word["mitigate"]["learned"])
        self.assertFalse(by_word["resilient"]["learned"])

    def test_learn_unknown_word_exits_2(self):
        vocab.main(["add", "--file", str(self.batch)])
        self.assertEqual(vocab.main(["learn", "nonexistent"]), 2)

    def test_unlearn_puts_it_back(self):
        vocab.main(["add", "--file", str(self.batch)])
        vocab.main(["learn", "mitigate"])
        self.assertEqual(vocab.main(["unlearn", "mitigate"]), 0)
        self.assertFalse(vocab.find_word(vocab.load(), "mitigate")["learned"])

    def test_learn_accepts_multiple_words(self):
        vocab.main(["add", "--file", str(self.batch)])
        self.assertEqual(vocab.main(["learn", "resilient", "mitigate"]), 0)
        self.assertTrue(all(w["learned"] for w in vocab.load()["words"]))

    def test_report_writes_file_and_heartbeat(self):
        vocab.main(["add", "--file", str(self.batch)])
        self.assertEqual(vocab.main(["report"]), 0)
        files = list(vocab.REPORT_DIR.glob("vocab-*.html"))
        self.assertEqual(len(files), 1)
        self.assertIn("还没背的", files[0].read_text(encoding="utf-8"))
        info = json.loads((vocab.LOG_DIR / "last-report.json").read_text(encoding="utf-8"))
        self.assertEqual(info["words"], 2)

    def test_stats_and_list_do_not_crash(self):
        vocab.main(["add", "--file", str(self.batch)])
        for argv in (["stats"], ["list"], ["list", "--all"], ["list", "--learned"], ["weeks"]):
            self.assertEqual(vocab.main(argv), 0, argv)

    def test_doctor_never_crashes(self):
        self.assertIn(vocab.main(["doctor"]), (0, 1))
        vocab.main(["add", "--file", str(self.batch)])
        vocab.main(["report"])
        self.assertEqual(vocab.main(["doctor"]), 0)

    def test_star_and_unstar(self):
        vocab.main(["add", "--file", str(self.batch)])
        self.assertEqual(vocab.main(["star", "mitigate"]), 0)
        self.assertTrue(vocab.find_word(vocab.load(), "mitigate")["starred"])
        self.assertEqual(vocab.main(["star", "nonexistent"]), 2)
        self.assertEqual(vocab.main(["unstar", "mitigate"]), 0)
        self.assertFalse(vocab.find_word(vocab.load(), "mitigate")["starred"])

    def test_learn_alias_kill(self):
        vocab.main(["add", "--file", str(self.batch)])
        self.assertEqual(vocab.main(["kill", "resilient"]), 0)
        self.assertTrue(vocab.find_word(vocab.load(), "resilient")["learned"])

    def test_add_tolerates_markdown_fence(self):
        """聊天机器人返回的 JSON 常被 ```json 包着，不能因此报错。"""
        fenced = self.tmp / "fenced.json"
        fenced.write_text('```json\n[{"word": "nuance", "zh": "细微差别"}]\n```\n', encoding="utf-8")
        self.assertEqual(vocab.main(["add", "--file", str(fenced)]), 0)
        self.assertIsNotNone(vocab.find_word(vocab.load(), "nuance"))


if __name__ == "__main__":
    unittest.main()
