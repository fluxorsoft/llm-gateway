"""Your own key for the developers' door, from your team's project, with your Google sign-in. Nothing is typed or stored.

  onedoorai key --project <team project>            prints your key (your handle comes from your gcloud account)
  export OPENAI_API_KEY=$(onedoorai key --project team-project)        for example, for a tool that reads a variable

Only you can read your secret (llm-key-<handle>); the platform team's runner writes it.
"""
import base64
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


def gcloud(*args):
    exe = shutil.which("gcloud.cmd") or shutil.which("gcloud")
    if not exe:
        raise SystemExit("gcloud is needed: sign in with gcloud auth login")
    return subprocess.run([exe, *args], check=True, capture_output=True, text=True).stdout.strip()


def handle(email):
    """The same rule as the settings: the part before the @, lower case, other characters as hyphens, at most 30."""
    return re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].lower()).strip("-")[:30]


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="onedoorai key", description=__doc__.split("\n\n")[0])
    p.add_argument("--project", required=True, help="your team's Google Cloud project")
    p.add_argument("--person", help="your handle (default: from your gcloud account)")
    a = p.parse_args(argv)
    who = a.person or handle(gcloud("config", "get-value", "account"))
    url = f"https://secretmanager.googleapis.com/v1/projects/{a.project}/secrets/llm-key-{who}/versions/latest:access"
    r = urllib.request.Request(url, headers={"Authorization": "Bearer " + gcloud("auth", "print-access-token")})
    try:
        data = json.load(urllib.request.urlopen(r, timeout=30))["payload"]["data"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"no key for {who} in {a.project} ({e.code}): ask your platform team to add you to the team's members") from None
    sys.stdout.write(base64.b64decode(data).decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
