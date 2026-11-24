"""The price check's comparison, without BigQuery: python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from onedoorai.pricecheck import compare  # noqa: E402


class PriceCheckTest(unittest.TestCase):
    def test_levels(self):
        got = {l["project"]: (l["difference_percent"], l["level"]) for l in compare(
            {"a": 10.0, "b": 10.0, "c": 5.0}, {"a": 10.5, "b": 13.0}, above=10)}
        self.assertEqual(got, {"a": (4.8, "ok"), "b": (23.1, "drift"), "c": (100.0, "drift")})


if __name__ == "__main__":
    unittest.main()
