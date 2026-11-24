"""Models near their review date: python -m unittest discover -s tests"""
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from onedoorai.models import due  # noqa: E402

CAT = Path(__file__).resolve().parents[1].joinpath("settings/example/catalogue.yaml").read_text(encoding="utf-8")
TEAMS = {t: Path(__file__).resolve().parents[1].joinpath(f"settings/example/teams/{t}.yaml").read_text(encoding="utf-8") for t in ("orders", "support")}


class ModelsTest(unittest.TestCase):
    def test_due_models_and_their_teams(self):
        got = due(CAT, TEAMS, datetime.date(2027, 1, 15), days=30)
        self.assertEqual([(d["model"], d["days_left"], d["teams"]) for d in got], [("gemini-3.5-flash", 17, ["orders", "support"])])
        self.assertEqual(due(CAT, TEAMS, datetime.date(2026, 11, 1), days=30), [])


if __name__ == "__main__":
    unittest.main()
