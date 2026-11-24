"""Key rotation's decisions and edits, without Git: python -m unittest discover -s tests"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from onedoorai import rotate  # noqa: E402

DAY = 86400


class RotateTest(unittest.TestCase):
    def test_decisions(self):
        now = 100 * DAY
        teams = {"fresh": {"keys": [2], "key_days": 30}, "due": {"keys": [2], "key_days": 30},
                 "pair_new": {"keys": [2, 3], "key_days": 30}, "pair_old": {"keys": [2, 3], "key_days": 30}}
        changed = {"fresh": now - 10 * DAY, "due": now - 24 * DAY, "pair_new": now - 3600, "pair_old": now - 2 * DAY}
        got = {c["team"]: (c["step"], c["next"]) for c in rotate.decide(teams, changed, now)}
        self.assertEqual(got, {"due": ("add", [2, 3]), "pair_old": ("remove", [3])})

    def test_now_whatever_the_age(self):
        now = 100 * DAY
        teams = {"fresh": {"keys": [2], "key_days": 30}, "pair_new": {"keys": [2, 3], "key_days": 30}, "other": {"keys": [1], "key_days": 30}}
        changed = {"fresh": now - 3600, "pair_new": now - 3600, "other": now - 3600}
        got = {c["team"]: (c["step"], c["next"]) for c in rotate.decide(teams, changed, now, forced={"fresh", "pair_new"})}
        self.assertEqual(got, {"fresh": ("add", [2, 3]), "pair_new": ("remove", [3])})

    def test_the_edit_keeps_everything_else(self):
        d = Path(tempfile.mkdtemp())
        (d / "teams").mkdir()
        f = d / "teams" / "orders.yaml"
        f.write_text("owner: a@example.com\nkeys: [2]                       # to rotate: add 2\nkey_days: 30\n", encoding="utf-8")
        t = rotate.read(d)["orders"]
        self.assertEqual((t["keys"], t["key_days"]), ([2], 30))
        rotate.write(f, [2, 3])
        self.assertEqual(f.read_text(encoding="utf-8"), "owner: a@example.com\nkeys: [2, 3]                       # to rotate: add 2\nkey_days: 30\n")


if __name__ == "__main__":
    unittest.main()
