"""存储层测试：原子写入、自动备份、BOM 容错、幂等导入。"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vocab  # noqa: E402


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = (vocab.DATA_FILE, vocab.BACKUP_DIR, vocab.LOG_DIR, vocab.REPORT_DIR)
        vocab.DATA_FILE = self.tmp / "data" / "words.json"
        vocab.BACKUP_DIR = self.tmp / "data" / "backup"
        vocab.LOG_DIR = self.tmp / "logs"
        vocab.REPORT_DIR = self.tmp / "reports"

    def tearDown(self):
        vocab.DATA_FILE, vocab.BACKUP_DIR, vocab.LOG_DIR, vocab.REPORT_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_library_loads_as_empty(self):
        data = vocab.load()
        self.assertEqual(data["words"], [])

    def test_save_then_load_roundtrip(self):
        data = {"words": []}
        vocab.upsert(data, {"word": "nuance", "zh": "细微差别"})
        vocab.save(data)
        again = vocab.load()
        self.assertEqual(len(again["words"]), 1)
        self.assertEqual(again["words"][0]["word"], "nuance")
        self.assertEqual(again["words"][0]["zh"], "细微差别")

    def test_save_creates_backup_only_from_second_write(self):
        data = {"words": []}
        vocab.upsert(data, {"word": "a", "zh": "甲"})
        vocab.save(data)
        self.assertFalse(vocab.BACKUP_DIR.exists(), "首次写入不应产生备份")
        vocab.upsert(data, {"word": "b", "zh": "乙"})
        vocab.save(data)
        self.assertEqual(len(list(vocab.BACKUP_DIR.glob("words-*.json"))), 1)

    def test_load_tolerates_utf8_bom(self):
        vocab.DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"words": [{"word": "bom", "zh": "带 BOM"}]}, ensure_ascii=False)
        vocab.DATA_FILE.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))
        data = vocab.load()
        self.assertEqual(data["words"][0]["word"], "bom")

    def test_no_temp_file_left_behind(self):
        data = {"words": []}
        vocab.upsert(data, {"word": "c", "zh": "丙"})
        vocab.save(data)
        leftovers = list(vocab.DATA_FILE.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_backups_are_pruned(self):
        orig_max = vocab.MAX_BACKUPS
        vocab.MAX_BACKUPS = 3
        try:
            data = {"words": []}
            for i in range(6):
                vocab.upsert(data, {"word": f"w{i}", "zh": f"词{i}"})
                vocab.save(data)
            self.assertLessEqual(len(list(vocab.BACKUP_DIR.glob("words-*.json"))), 3)
        finally:
            vocab.MAX_BACKUPS = orig_max


if __name__ == "__main__":
    unittest.main()
