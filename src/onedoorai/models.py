"""Models near their review date, and the teams that use them.

  onedoorai models --settings settings [--days 30]

Prints one JSON line per catalogue model whose review_by is within --days (or past), with the teams that use it. The
onboarding repository's model-watch workflow opens an issue from these lines.
"""
import datetime
import json
import re
import sys
from pathlib import Path

REVIEW = re.compile(r'^  ([A-Za-z0-9._-]+):\s*$|^\s+review_by:\s*"?(\d{4}-\d{2}-\d{2})"?', re.M)
MODEL = re.compile(r"^\s+model:\s*([A-Za-z0-9._-]+)", re.M)


def due(catalogue_text, teams, today, days=30):
    """catalogue_text: the catalogue's YAML; teams: {team: team file text}. Returns the models to review."""
    out, current = [], None
    section = catalogue_text.split("\nmodels:", 1)[-1].split("\n\n", 1)[0] if "\nmodels:" in "\n" + catalogue_text else ""
    for name, date in REVIEW.findall("\n" + section):
        if name:
            current = name
        elif current and date:
            left = (datetime.date.fromisoformat(date) - today).days
            if left <= days:
                users = sorted(t for t, text in teams.items() if current in MODEL.findall(text))
                out.append({"model": current, "review_by": date, "days_left": left, "teams": users})
    return out


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="onedoorai models")
    p.add_argument("--settings", default="settings")
    p.add_argument("--days", type=int, default=30)
    a = p.parse_args(argv)
    cat = Path(a.settings, "catalogue.yaml").read_text(encoding="utf-8")
    teams = {f.stem: f.read_text(encoding="utf-8") for f in sorted(Path(a.settings, "teams").glob("*.yaml"))}
    for line in due(cat, teams, datetime.date.today(), a.days):
        print(json.dumps(line), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
