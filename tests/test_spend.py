"""onedoorai spend's line: python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from onedoorai import spend  # noqa: E402


class SpendTest(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(spend.line({"key_alias": "orders.bob", "spend": 0.043}), "orders.bob: spent 0.0430 USD")
        self.assertEqual(spend.line({"key_alias": "orders.ann", "spend": 1.2, "max_budget": 5, "budget_duration": "1d",
                                     "budget_reset_at": "2026-11-25T00:00:00+00:00"}),
                         "orders.ann: spent 1.2000 USD of 5.00 a day; resets 2026-11-25T00:00:00+00:00")
        self.assertIn("of 0.001 a day", spend.line({"key_alias": "a", "spend": 0.0054, "max_budget": 0.001, "budget_duration": "1d"}))


if __name__ == "__main__":
    unittest.main()
