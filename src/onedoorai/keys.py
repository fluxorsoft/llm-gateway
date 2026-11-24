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
import hashlib
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.request

MANAGED = re.compile(r"^[a-z][a-z0-9-]{1,19}-[0-9]+$")  # team-n
PERSON = re.compile(r"^[a-z][a-z0-9-]{1,19}\.[a-z0-9-]{1,30}$")  # team.handle: a member's own key
AGENT = re.compile(r"^[a-z][a-z0-9-]{1,19}~[a-z][a-z0-9-]{1,29}$")  # team~agent: an agent's own key
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
        agents = {a for a in existing.get(team, {}) if AGENT.match(a) and a.split("~", 1)[0] == team}
        wanted_agents = {f"{team}~{g['name']}": g for g in w.get("agents") or []}
        for a in sorted(set(wanted_agents) & agents):
            blocked, stop = bool(existing[team][a].get("blocked")), bool(wanted_agents[a].get("stopped"))
            steps.append(("stop" if stop and not blocked else "start" if blocked and not stop else "keep", team, a))
        steps += [("create", team, a) for a in sorted(set(wanted_agents) - agents)]
        steps += [("delete", team, a) for a in sorted(agents - set(wanted_agents))]
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

    def servers(self):
        """{name: server_id} of the tool servers LiteLLM knows."""
        try:
            got = self.call("GET", "/v1/mcp/server")
        except RuntimeError:
            return {}
        return {s.get("server_name") or s.get("alias"): s["server_id"] for s in (got if isinstance(got, list) else got.get("data", []))}

    def set_team_servers(self, team_id, permission):
        self.call("POST", "/team/update", {"team_id": team_id, "object_permission": permission})

    def set_key_permission(self, token, permission):
        self.call("POST", "/key/update", {"key": token, "object_permission": permission})

    def block(self, token, blocked=True):
        self.call("POST", "/key/block" if blocked else "/key/unblock", {"key": token})

    def create_agent(self, team_id, alias, models, days, agent, value=None):
        body = {"team_id": team_id, "key_alias": alias, "models": models, "duration": f"{days}d",
                "metadata": {"agent": agent["name"], "identity": agent["identity"]}}
        if value:
            body["key"] = value
        if agent.get("daily_usd"):
            body.update(max_budget=agent["daily_usd"], budget_duration="1d")
        if agent.get("rpm"):
            body["rpm_limit"] = agent["rpm"]
        if agent.get("permission"):
            body["object_permission"] = agent["permission"]
        return self.call("POST", "/key/generate", body)["key"]


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


def read_secret(project, secret, token=_metadata_token):
    """The newest version of a secret, or None when it has none yet."""
    r = urllib.request.Request(f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{secret}/versions/latest:access",
                               headers={"Authorization": "Bearer " + token()})
    try:
        return base64.b64decode(json.load(urllib.request.urlopen(r, timeout=30))["payload"]["data"]).decode()
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):  # no version yet
            return None
        raise


def retire_versions(project, secret, gone, token=_metadata_token):
    """Disables each enabled version of the secret whose value is a deleted key (LiteLLM keeps a key as its SHA-256, so
    `gone` is those hashes): a removed key's value is no longer readable. Returns the versions disabled."""
    base = f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{secret}/versions"
    def call(url, body=None):
        r = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                                   {"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(r, timeout=30))
    done = []
    for v in call(base + "?filter=state:ENABLED").get("versions", []):
        value = base64.b64decode(call(f"https://secretmanager.googleapis.com/v1/{v['name']}:access")["payload"]["data"])
        if hashlib.sha256(value).hexdigest() in gone:
            call(f"https://secretmanager.googleapis.com/v1/{v['name']}:disable", {})
            done.append(v["name"].rsplit("/", 1)[-1])
    return sorted(done, key=int)


def permission(servers, ids):
    """LiteLLM's object_permission for {server: [tools]}: the servers, and the tools of each ("*": all of them)."""
    return {"mcp_servers": sorted(servers), "mcp_tool_permissions": {ids[s]: sorted(t) for s, t in servers.items() if "*" not in t and s in ids}}


def run(wanted, admin, write=write_secret, apply=False, say=print, agents_secret=None, read=read_secret, retire=retire_versions):
    """Returns how many steps change something. Each step is reported as one JSON line, without any key's value."""
    teams = admin.teams()
    existing = {t: admin.keys(teams[t]["team_id"]) for t in wanted if t in teams}
    changes = 0
    # the agents' map: identity -> {key, team, agent}; the gateway reads it to accept an agent's Google identity
    project, secret = (agents_secret or "/").split("/", 1)
    agent_map = json.loads(read(project, secret) or "{}") if agents_secret and apply else {}
    map_changed = False
    gone = {}  # (project, secret) -> hashes of the keys deleted from it
    ids = admin.servers() if any(w.get("servers") for w in wanted.values()) else {}
    for team, w in sorted(wanted.items()):  # each team's tool servers, every time (it is cheap and keeps LiteLLM in step)
        if w.get("servers") and team in teams and apply:
            admin.set_team_servers(teams[team]["team_id"], permission(w["servers"], ids))
        for g in w.get("agents") or []:
            g["permission"] = permission(g.get("tools") or {}, ids) if g.get("tools") else {"mcp_servers": []}
    for action, team, alias in plan(wanted, existing):
        w, line = wanted[team], {"message": "keys", "team": team, "alias": alias, "action": action}
        if action in ("keep", "stop", "start") and AGENT.match(alias) and apply:  # an agent's tools and cap can change
            agent = next(g for g in w["agents"] if f"{team}~{g['name']}" == alias)
            entry = agent_map.get(agent["identity"])
            if entry is not None and entry.get("calls_per_task") != agent.get("calls_per_task"):
                entry["calls_per_task"] = agent.get("calls_per_task")
                map_changed = True
            if (existing[team][alias].get("object_permission") or {}).get("mcp_servers", []) != agent["permission"]["mcp_servers"] or agent["permission"].get("mcp_tool_permissions"):
                admin.set_key_permission(existing[team][alias]["token"], agent["permission"])
        if action != "keep":
            changes += 1
        if action == "keep":
            line["expires"] = existing[team][alias].get("expires")
        elif not apply:
            line["action"] = "would " + action
        elif team not in teams:
            line["action"], line["why"] = "skipped", "the team is not in LiteLLM yet"
        elif action in ("stop", "start"):
            admin.block(existing[team][alias]["token"], action == "stop")
        elif action == "create" and AGENT.match(alias):
            agent = next(g for g in w["agents"] if f"{team}~{g['name']}" == alias)
            value = "sk-" + secrets.token_urlsafe(36) if w.get("maker", "litellm") == "terraform" else None
            made = admin.create_agent(teams[team]["team_id"], alias, teams[team].get("models") or w.get("models") or [], w["days"], agent, value)
            if agent.get("stopped"):
                admin.block(admin.keys(teams[team]["team_id"])[alias]["token"])
            agent_map[agent["identity"]] = {"key": made, "team": team, "agent": agent["name"], "calls_per_task": agent.get("calls_per_task")}
            map_changed = True
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
            if existing[team][alias].get("token") and not AGENT.match(alias):
                where = (w["project"], "llm-key-" + alias.split(".", 1)[1] if "." in alias else "llm-gateway-key")
                gone.setdefault(where, set()).add(existing[team][alias]["token"])
            if AGENT.match(alias):
                agent_map = {i: v for i, v in agent_map.items() if f"{v['team']}~{v['agent']}" != alias}
                map_changed = True
        say(json.dumps(line), flush=True)
    for (p, sec), hashes in sorted(gone.items()):
        try:
            say(json.dumps({"message": "keys", "action": "old values disabled", "secret": sec, "project": p,
                            "versions": retire(p, sec, hashes)}), flush=True)
        except Exception as e:  # the key is already dead; the value staying readable is untidy, not unsafe
            say(json.dumps({"message": "keys", "action": "old values not disabled", "secret": sec, "project": p, "why": type(e).__name__}), flush=True)
    if apply and agents_secret and map_changed:
        version = write(project, json.dumps(agent_map), secret)
        say(json.dumps({"message": "keys", "action": "agents map written", "agents": len(agent_map), "secret_version": version}), flush=True)
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
    changes = run(wanted, admin, apply=a.apply, agents_secret=os.environ.get("AGENTS_SECRET"))
    return 2 if changes and not a.apply else 0


if __name__ == "__main__":
    sys.exit(main())
