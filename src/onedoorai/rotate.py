"""Key rotation as reviewed changes to the team files, in two steps:

  1. a team with one key, within LEAD days of key_days, gets the next key:   keys: [2]    ->  keys: [2, 3]
     (the deploy makes key 3, writes it to the team's secret, and restarts the services listed under restart:)
  2. a day later, the old key goes:                                           keys: [2, 3] ->  keys: [3]

  onedoorai rotate --settings settings [--write] [--lead 7]

The age of a team's keys is when its `keys:` line last changed in Git. Prints one JSON line per change; with --write, edits
the files (comments and everything else are kept). Exit 0 either way; the workflow opens a pull request when files changed.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

KEYS = re.compile(r"^(keys:\s*\[)([0-9,\s]*)(\].*)$", re.M)
DAYS = re.compile(r"^key_days:\s*([0-9]+)", re.M)
DAY = 86400


def decide(teams, changed_at, now, lead=7):
    """teams: {team: {"keys": [n], "key_days": d}}; changed_at: {team: epoch seconds}. Returns the changes."""
    out = []
    for team, t in sorted(teams.items()):
        keys, age = sorted(t["keys"]), (now - changed_at.get(team, now)) / DAY
        if len(keys) == 1 and age >= t["key_days"] - lead:
            out.append({"team": team, "step": "add", "keys": keys, "next": keys + [keys[-1] + 1], "age_days": round(age, 1)})
        elif len(keys) > 1 and age >= 1:
            out.append({"team": team, "step": "remove", "keys": keys, "next": keys[-1:], "age_days": round(age, 1)})
    return out


def read(folder):
    teams = {}
    for f in sorted(Path(folder, "teams").glob("*.yaml")):
        text = f.read_text(encoding="utf-8")
        k, d = KEYS.search(text), DAYS.search(text)
        if k and d:
            teams[f.stem] = {"keys": [int(x) for x in k.group(2).replace(" ", "").split(",") if x], "key_days": int(d.group(1)), "file": f}
    return teams


def last_change(path):
    r = subprocess.run(["git", "log", "-1", "--format=%ct", "-G", "^keys:", "--", path.name], cwd=path.parent,
                       capture_output=True, text=True)
    return int(r.stdout.strip()) if r.stdout.strip() else None


def write(path, keys):
    text = path.read_text(encoding="utf-8")
    path.write_text(KEYS.sub(lambda m: f"{m.group(1)}{', '.join(map(str, keys))}{m.group(3)}", text, count=1), encoding="utf-8")


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="onedoorai rotate")
    p.add_argument("--settings", default="settings")
    p.add_argument("--write", action="store_true")
    p.add_argument("--lead", type=int, default=7, help="days before key_days to add the next key")
    a = p.parse_args(argv)
    teams = read(a.settings)
    changed = {t: c for t, v in teams.items() if (c := last_change(v["file"])) is not None}
    for change in decide(teams, changed, time.time(), a.lead):
        if a.write:
            write(teams[change["team"]]["file"], change["next"])
        print(json.dumps(change), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
