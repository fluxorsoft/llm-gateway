"""Team keys: made and adopted by name, written only to the team's own secret.

The settings list each team's key numbers (`keys: [1, 2]`); key `n` of team `orders` is called `orders-n` in LiteLLM. This step
makes the keys that are missing, keeps those that exist (including keys an older version made), and deletes the team keys that
are no longer listed. A key's value is written to the team's secret and nowhere else: never to Terraform state, files or logs.

Who chooses the value (`key_maker` in the catalogue):
  litellm     LiteLLM makes it (the default for settings schema 2 and later)
  terraform   we make it, as 1.0.0 did (the default for schema 1 files, until the platform team switches)

  onedoorai keys --wanted wanted.json [--apply]     wanted.json is the settings step's `keys` output
Without --apply it only reports what it would do, and exits 2 when anything differs (the drift check).
"""
import base64
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request

MANAGED = re.compile(r"^[a-z][a-z0-9-]{1,19}-[0-9]+$")  # team-n
PERSON = re.compile(r"^[a-z][a-z0-9-]{1,19}\.[a-z0-9-]{1,30}$")  # team.handle: a member's own key
# any other key (made by hand, or by an agent's step) is never touched here


def plan(wanted, existing):
    """What to do. wanted: {team: {"keys": [n], ...}}; existing: {team: {alias: key}}. Returns a list of (action, team, alias)."""
    steps = []
    for team, w in sorted(wanted.items()):
        have = {a for a in existing.get(team, {}) if MANAGED.match(a) and a.rsplit("-", 1)[0] == team}
        want = {f"{team}-{n}" for n in w["keys"]}
        steps += [("keep", team, a) for a in sorted(want & have)]
        steps += [("create", team, a) for a in sorted(want - have, key=lambda a: int(a.rsplit("-", 1)[1]))]
        steps += [("delete", team, a) for a in sorted(have - want)]
        people = {a for a in existing.get(team, {}) if PERSON.match(a) and a.split(".", 1)[0] == team}
        wanted_people = {f"{team}.{m['handle']}" for m in w.get("members") or []}
        steps += [("keep", team, a) for a in sorted(wanted_people & people)]
        steps += [("create", team, a) for a in sorted(wanted_people - people)]
        steps += [("delete", team, a) for a in sorted(people - wanted_people)]
    return steps


class Admin:
    """LiteLLM's admin API, through `onedoorai admin` inside the network."""

    def __init__(self, url, master_key):
        self.url, self.key = url.rstrip("/"), master_key

    def call(self, method, path, body=None):
        r = urllib.request.Request(self.url + path, json.dumps(body).encode() if body is not None else None,
                                   {"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(r, timeout=60) as a:
                return json.load(a)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path}: {e.code} {e.read().decode(errors='replace')[:200]}") from None

    def teams(self):
        return {t["team_alias"]: t for t in self.call("GET", "/team/list")}

    def keys(self, team_id):
        out, page = {}, 1
        while True:
            r = self.call("GET", f"/key/list?team_id={team_id}&return_full_object=true&size=100&page={page}")
            for k in r.get("keys", []):
                if k.get("key_alias"):
                    out[k["key_alias"]] = k
            if page >= int(r.get("total_pages") or 1):
                return out
            page += 1

    def create(self, team_id, alias, models, days, value=None, user_id=None, daily_usd=None):
        body = {"team_id": team_id, "key_alias": alias, "models": models, "duration": f"{days}d"}
        if value:
            body["key"] = value
        if user_id:
            body["user_id"] = user_id
        if daily_usd:
            body.update(max_budget=daily_usd, budget_duration="1d")
        return self.call("POST", "/key/generate", body)["key"]

    def ensure_member(self, team_id, user_id):
        """The person exists, and is a member of the team, so the team's member budget applies to them."""
        for path, body in (("/user/new", {"user_id": user_id, "user_email": user_id, "user_role": "internal_user"}),
                           ("/team/member_add", {"team_id": team_id, "member": {"role": "user", "user_id": user_id}})):
            try:
                self.call("POST", path, body)
            except RuntimeError as e:
                if "exist" not in str(e).lower() and "already" not in str(e).lower():
                    raise

    def delete(self, alias):
        self.call("POST", "/key/delete", {"key_aliases": [alias]})


def _metadata_token():
    r = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                               headers={"Metadata-Flavor": "Google"})
    return json.load(urllib.request.urlopen(r, timeout=10))["access_token"]


def write_secret(project, value, secret="llm-gateway-key", token=_metadata_token):  # a person's: llm-key-<handle>
    """Adds the value as the newest version of the team's secret (Google Cloud Secret Manager). Returns the version."""
    r = urllib.request.Request(
        f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{secret}:addVersion",
        json.dumps({"payload": {"data": base64.b64encode(value.encode()).decode()}}).encode(),
        {"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=30))["name"].rsplit("/", 1)[-1]


def run(wanted, admin, write=write_secret, apply=False, say=print):
    """Returns how many steps change something. Each step is reported as one JSON line, without any key's value."""
    teams = admin.teams()
    existing = {t: admin.keys(teams[t]["team_id"]) for t in wanted if t in teams}
    changes = 0
    for action, team, alias in plan(wanted, existing):
        w, line = wanted[team], {"message": "keys", "team": team, "alias": alias, "action": action}
        if action != "keep":
            changes += 1
        if action == "keep":
            line["expires"] = existing[team][alias].get("expires")
        elif not apply:
            line["action"] = "would " + action
        elif team not in teams:
            line["action"], line["why"] = "skipped", "the team is not in LiteLLM yet"
        elif action == "create":
            value = "sk-" + secrets.token_urlsafe(36) if w.get("maker", "litellm") == "terraform" else None
            person = next((m for m in w.get("members") or [] if f"{team}.{m['handle']}" == alias), None)
            models = teams[team].get("models") or w.get("models") or []
            if person:
                admin.ensure_member(teams[team]["team_id"], person["id"])
                made = admin.create(teams[team]["team_id"], alias, models, w["days"], value, person["id"], person.get("daily_usd"))
            else:
                made = admin.create(teams[team]["team_id"], alias, models, w["days"], value)
            try:
                line["secret_version"] = (write(w["project"], made, "llm-key-" + person["handle"]) if person
                                          else write(w["project"], made))
            except Exception as e:  # no key that nobody can use
                admin.delete(alias)
                line["action"], line["why"] = "create failed: the secret could not be written, the key was deleted", type(e).__name__
        elif action == "delete":
            admin.delete(alias)
        say(json.dumps(line), flush=True)
    return changes


def probe(admin):
    """True when LiteLLM still accepts a key value we choose. Makes one short-lived key and deletes it."""
    team = next(iter(admin.teams().values()), None)
    if team is None:
        return True
    alias = "probe-" + secrets.token_hex(4)
    try:
        admin.create(team["team_id"], alias, team.get("models") or [], 1, "sk-" + secrets.token_urlsafe(36))
    except RuntimeError as e:
        if "403" in str(e) or "disabled" in str(e).lower():
            return False
        raise
    admin.delete(alias)
    return True


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="onedoorai keys")
    p.add_argument("--wanted", required=True, help="JSON: {team: {keys, days, project, maker}}")
    p.add_argument("--probe", action="store_true", help="only check that LiteLLM accepts the keys' way of making values")
    p.add_argument("--apply", action="store_true", help="make the changes (default: report only)")
    p.add_argument("--admin", default=os.environ.get("ADMIN_PROXY", "http://127.0.0.1:9000"))
    a = p.parse_args(argv)
    master = os.environ.get("TF_VAR_master_key") or os.environ.get("LITELLM_MASTER_KEY")
    if not master:
        raise SystemExit("the master key is needed: TF_VAR_master_key or LITELLM_MASTER_KEY")
    wanted = json.load(open(a.wanted))
    admin = Admin(a.admin, master)
    if a.probe:
        if any(w.get("maker") == "terraform" for w in wanted.values()) and not probe(admin):
            print(json.dumps({"message": "keys", "action": "refused", "why": "this LiteLLM no longer accepts key values we choose: set key_maker: litellm first"}), flush=True)
            return 3
        print(json.dumps({"message": "keys", "action": "probe passed"}), flush=True)
        return 0
    changes = run(wanted, admin, apply=a.apply)
    return 2 if changes and not a.apply else 0


if __name__ == "__main__":
    sys.exit(main())
