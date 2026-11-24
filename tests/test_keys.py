"""The key step, without LiteLLM or Google Cloud: python -m unittest discover -s tests"""
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from onedoorai import keys  # noqa: E402


class FakeAdmin:
    def __init__(self, existing):
        self.existing = existing  # {team: {alias: {...}}}
        self.created, self.deleted = [], []

    def teams(self):
        return {t: {"team_id": "id-" + t, "models": [t + "-chat"]} for t in ("orders", "support")}

    def keys(self, team_id):
        return dict(self.existing.get(team_id[3:], {}))

    def create(self, team_id, alias, models, days, value=None):
        self.created.append((alias, models, days, value))
        return value or "sk-made-by-litellm"

    def delete(self, alias):
        self.deleted.append(alias)


def wanted(**teams):
    return {t: {"keys": k, "days": 30, "project": t + "-project", **({"maker": m} if m else {})} for t, (k, m) in teams.items()}


class KeysTest(unittest.TestCase):
    def run_keys(self, admin, w, apply=True, write=None):
        out, written = io.StringIO(), []
        write = write or (lambda project, value: written.append((project, value)) or "7")
        changes = keys.run(w, admin, write=write, apply=apply, say=lambda s, flush=False: out.write(s + "\n"))
        return changes, [json.loads(x) for x in out.getvalue().splitlines()], written

    def test_existing_keys_are_adopted_and_nothing_is_written(self):
        a = FakeAdmin({"orders": {"orders-2": {"expires": "x"}}, "support": {"support-1": {}}})
        changes, lines, written = self.run_keys(a, wanted(orders=([2], None), support=([1], None)))
        self.assertEqual(changes, 0)
        self.assertEqual({l["action"] for l in lines}, {"keep"})
        self.assertEqual((a.created, a.deleted, written), ([], [], []))

    def test_a_new_key_is_made_by_litellm_and_written_to_the_team_secret_only(self):
        a = FakeAdmin({"orders": {"orders-2": {}}})
        changes, lines, written = self.run_keys(a, wanted(orders=([2, 3], None)))
        self.assertEqual(a.created, [("orders-3", ["orders-chat"], 30, None)])
        self.assertEqual(written, [("orders-project", "sk-made-by-litellm")])
        self.assertNotIn("sk-", json.dumps(lines))  # no value in the report

    def test_the_older_way_chooses_the_value(self):
        a = FakeAdmin({})
        self.run_keys(a, wanted(orders=([1], "terraform")))
        self.assertTrue(a.created[0][3].startswith("sk-") and len(a.created[0][3]) > 40)

    def test_a_key_no_longer_listed_is_deleted_and_others_are_never_touched(self):
        a = FakeAdmin({"orders": {"orders-1": {}, "orders-2": {}, "orders-ann": {}, "support-1": {}, "someone": {}}})
        changes, lines, _ = self.run_keys(a, wanted(orders=([2], None)))
        self.assertEqual(a.deleted, ["orders-1"])

    def test_without_apply_nothing_changes_and_the_difference_is_counted(self):
        a = FakeAdmin({"orders": {"orders-1": {}}})
        changes, lines, written = self.run_keys(a, wanted(orders=([2], None)), apply=False)
        self.assertEqual(changes, 2)
        self.assertEqual((a.created, a.deleted, written), ([], [], []))
        self.assertEqual(sorted(l["action"] for l in lines), ["would create", "would delete"])

    def test_if_the_secret_cannot_be_written_the_key_is_deleted(self):
        a = FakeAdmin({})

        def broken(project, value):
            raise OSError("denied")
        _, lines, _ = self.run_keys(a, wanted(orders=([1], None)), write=broken)
        self.assertEqual(a.deleted, ["orders-1"])
        self.assertIn("create failed", lines[0]["action"])


if __name__ == "__main__":
    unittest.main()
