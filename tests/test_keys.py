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

    def create(self, team_id, alias, models, days, value=None, user_id=None, daily_usd=None):
        self.created.append((alias, models, days, value) if user_id is None else (alias, user_id, daily_usd))
        return value or "sk-made-by-litellm"

    def create_agent(self, team_id, alias, models, days, agent, value=None):
        self.created.append((alias, agent["identity"], agent.get("daily_usd")))
        return "sk-agent-" + agent["name"]

    def servers(self):
        return {"files": "id-files"}

    def set_team_servers(self, team_id, permission):
        self.team_servers = getattr(self, "team_servers", []) + [(team_id, permission)]

    def set_key_permission(self, token, permission):
        self.key_permissions = getattr(self, "key_permissions", []) + [(token, permission)]

    def block(self, token, blocked=True):
        self.blocked = getattr(self, "blocked", []) + [(token, blocked)]

    def ensure_member(self, team_id, user_id):
        self.members = getattr(self, "members", []) + [(team_id, user_id)]

    def delete(self, alias):
        self.deleted.append(alias)


def wanted(**teams):
    return {t: {"keys": k, "days": 30, "project": t + "-project", **({"maker": m} if m else {})} for t, (k, m) in teams.items()}


class KeysTest(unittest.TestCase):
    def run_keys(self, admin, w, apply=True, write=None):
        out, written = io.StringIO(), []
        write = write or (lambda project, value, secret="llm-gateway-key": written.append((project, value) if secret == "llm-gateway-key" else (project, secret)) or "7")
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


    def test_people_get_their_own_key_in_their_own_secret(self):
        a = FakeAdmin({"orders": {"orders-2": {}, "orders.gone": {}}})
        w = wanted(orders=([2], None))
        w["orders"]["members"] = [{"id": "ann.lee@example.com", "handle": "ann-lee", "daily_usd": 5}]
        _, lines, written = self.run_keys(a, w)
        self.assertEqual(a.created, [("orders.ann-lee", "ann.lee@example.com", 5)])
        self.assertEqual(a.members, [("id-orders", "ann.lee@example.com")])
        self.assertEqual(written, [("orders-project", "llm-key-ann-lee")])
        self.assertEqual(a.deleted, ["orders.gone"])  # no longer a member

    def test_agents_get_keys_a_map_and_a_stop_switch(self):
        a = FakeAdmin({"orders": {"orders~old": {"token": "t-old"}, "orders~paused": {"token": "t-p", "blocked": True},
                                  "orders~runaway": {"token": "t-r"}}})
        w = wanted(orders=([], None))
        w["orders"]["agents"] = [{"name": "sorter", "identity": "sorter@p.iam.gserviceaccount.com", "daily_usd": 2},
                                 {"name": "paused", "identity": "paused@p.iam.gserviceaccount.com"},
                                 {"name": "runaway", "identity": "runaway@p.iam.gserviceaccount.com", "stopped": True}]
        stored = {"gw/agents": json.dumps({"old@p.iam.gserviceaccount.com": {"key": "k", "team": "orders", "agent": "old"}})}
        out = io.StringIO()
        keys.run(w, a, apply=True, agents_secret="gw/agents", say=lambda s, flush=False: out.write(s + "\n"),
                 read=lambda p, s: stored.get(f"{p}/{s}"), write=lambda p, v, s="": stored.__setitem__(f"{p}/{s}", v) or "3")
        self.assertEqual(a.created, [("orders~sorter", "sorter@p.iam.gserviceaccount.com", 2)])
        self.assertEqual(a.deleted, ["orders~old"])
        self.assertEqual(sorted(a.blocked), [("t-p", False), ("t-r", True)])  # paused starts again, runaway stops
        self.assertEqual(json.loads(stored["gw/agents"]), {"sorter@p.iam.gserviceaccount.com": {"key": "sk-agent-sorter", "team": "orders", "agent": "sorter", "calls_per_task": None}})
        self.assertNotIn("sk-agent", out.getvalue())

    def test_tool_servers_for_the_team_and_each_agent(self):
        a = FakeAdmin({"orders": {"orders~reader": {"token": "t-r", "object_permission": {"mcp_servers": []}}}})
        w = wanted(orders=([], None))
        w["orders"]["servers"] = {"files": ["read", "list"]}
        w["orders"]["agents"] = [{"name": "reader", "identity": "r@p.iam.gserviceaccount.com", "tools": {"files": ["read"]}}]
        self.run_keys(a, w)
        self.assertEqual(a.team_servers, [("id-orders", {"mcp_servers": ["files"], "mcp_tool_permissions": {"id-files": ["list", "read"]}})])
        self.assertEqual(a.key_permissions, [("t-r", {"mcp_servers": ["files"], "mcp_tool_permissions": {"id-files": ["read"]}})])

    def test_an_agent_without_tools_gets_none(self):
        from onedoorai.keys import permission
        self.assertEqual(permission({"files": ["*"]}, {"files": "x"}), {"mcp_servers": ["files"], "mcp_tool_permissions": {}})

    def test_the_probe_says_whether_chosen_values_are_still_accepted(self):
        class Refusing(FakeAdmin):
            def create(self, *a, **k):
                raise RuntimeError("POST /key/generate: 403 Custom API key values are disabled by your administrator")
        a = FakeAdmin({})
        self.assertTrue(keys.probe(a))
        self.assertEqual(len(a.deleted), 1)  # the probe key is removed
        self.assertFalse(keys.probe(Refusing({})))


class HandleTest(unittest.TestCase):
    def test_the_helper_and_the_settings_agree(self):
        from onedoorai.key import handle
        self.assertEqual(handle("Ann.Lee@example.com"), "ann-lee")
        self.assertEqual(handle("x__y+z@example.com"), "x-y-z")


if __name__ == "__main__":
    unittest.main()
