"""The admin record, without LiteLLM or Google Cloud: python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from onedoorai.audit import record, scrub  # noqa: E402


class AuditTest(unittest.TestCase):
    def test_no_secret_is_written(self):
        got = scrub({"models": [{"litellm_params": {"api_key": "sk-x", "model": "m"}}], "token": "abc", "key": None})
        self.assertEqual(got, {"key": None, "models": [{"litellm_params": {"api_key": "(hidden)", "model": "m"}}], "token": "(hidden)"})

    def test_each_record_carries_the_one_before(self):
        a = record({"teams": [1]}, None, "d1")
        b = record({"teams": [1]}, a["digest"], "d2")
        c = record({"teams": [2]}, a["digest"], "d2")
        self.assertEqual(b["previous"], a["digest"])
        self.assertNotEqual(b["digest"], a["digest"])  # the same settings still chain
        self.assertNotEqual(b["digest"], c["digest"])  # another history gives another fingerprint


if __name__ == "__main__":
    unittest.main()
