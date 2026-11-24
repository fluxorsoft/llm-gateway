"""Budget levels, without LiteLLM: python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from onedoorai.budgets import lines  # noqa: E402


class BudgetsTest(unittest.TestCase):
    def test_levels(self):
        got = {l["team"]: l["level"] for l in lines([
            {"team_alias": "a", "max_budget": 10, "spend": 7.9},
            {"team_alias": "b", "max_budget": 10, "spend": 8},
            {"team_alias": "c", "max_budget": 10, "spend": 10.4},
            {"team_alias": "d", "max_budget": None, "spend": 99},
        ])}
        self.assertEqual(got, {"a": "ok", "b": "80", "c": "100"})


if __name__ == "__main__":
    unittest.main()
