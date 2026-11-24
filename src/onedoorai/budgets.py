"""Budget levels: one line per team saying how much of its monthly budget is spent. Alerts are built on these lines.

  onedoorai budgets          (in the runner, through `onedoorai admin`)

Each line: {"message": "llm budget", "team", "spend_usd", "budget_usd", "percent", "level"}, where level is "ok", "80" or "100".
The spend is LiteLLM's own count for the budget period, which is what stops a team at 100 per cent.
"""
import json
import os
import sys

from .keys import Admin


def level(percent):
    return "100" if percent >= 100 else "80" if percent >= 80 else "ok"


def lines(teams):
    """teams: LiteLLM's team list. Teams without a budget are left out."""
    out = []
    for t in sorted(teams, key=lambda t: t.get("team_alias") or ""):
        budget = t.get("max_budget")
        if not budget:
            continue
        spend = float(t.get("spend") or 0)
        percent = round(100 * spend / float(budget), 1)
        out.append({"message": "llm budget", "team": t.get("team_alias"), "spend_usd": round(spend, 4),
                    "budget_usd": float(budget), "percent": percent, "level": level(percent),
                    "severity": "WARNING" if percent >= 80 else "INFO"})
    return out


def main(argv=None):
    master = os.environ.get("TF_VAR_master_key") or os.environ.get("LITELLM_MASTER_KEY")
    if not master:
        raise SystemExit("the master key is needed: TF_VAR_master_key or LITELLM_MASTER_KEY")
    admin = Admin(os.environ.get("ADMIN_PROXY", "http://127.0.0.1:9000"), master)
    for line in lines(admin.call("GET", "/team/list")):
        print(json.dumps(line), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
