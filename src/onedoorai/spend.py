"""Your own spend on the developers' door: today's spend, your daily limit, and when it resets.

  onedoorai spend --door https://dev.llm.internal.example.com --project <team project>
The key is read with `onedoorai key` (your Google sign-in); the door answers only about the key that asks.
"""
import json
import os
import ssl
import sys
import urllib.request

from .key import main as _key  # noqa: F401  (the same rules)


def line(info):
    """One line about a key: what it spent, its limit and when the limit resets."""
    limit, every = info.get("max_budget"), info.get("budget_duration")
    out = f"{info.get('key_alias')}: spent {info.get('spend') or 0:.4f} USD"
    if limit:
        out += f" of {limit:.2f}" if limit >= 0.01 else f" of {limit:g}"
        out += f" a {'day' if every == '1d' else every}" if every else ""
    if info.get("budget_reset_at"):
        out += f"; resets {info['budget_reset_at']}"
    return out


def main(argv=None):
    import argparse
    import contextlib
    import io
    p = argparse.ArgumentParser(prog="onedoorai spend")
    p.add_argument("--door", required=True, help="the developers' door's address")
    p.add_argument("--project", required=True, help="your team's Google Cloud project")
    p.add_argument("--ca", default=os.environ.get("LLM_CA_PEM_FILE"), help="the door's CA certificate, for a private one")
    a = p.parse_args(argv)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        _key(["--project", a.project])
    r = urllib.request.Request(a.door.rstrip("/") + "/key/info", headers={"Authorization": "Bearer " + out.getvalue().strip()})
    info = json.load(urllib.request.urlopen(r, timeout=30, context=ssl.create_default_context(cafile=a.ca)))["info"]
    print(line(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
