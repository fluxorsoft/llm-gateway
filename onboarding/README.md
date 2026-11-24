# LLM gateway: onboarding

This organisation's settings for [llm-gateway](https://github.com/fluxorsoft/llm-gateway): which release runs, which models and
places are allowed, and one file per team. The gateway's code stays in its own repository; nothing here is code.

Make a **private** repository from this folder: its settings name projects, and its workflow logs show them.

| Path | What it is | Who changes it |
|---|---|---|
| `GATEWAY_VERSION` | The release this organisation runs (also in the two `uses:` lines of `deploy.yml`) | Platform team |
| `settings/catalogue.yaml` | Approved models, places, residency rules and limits | Platform team, with security |
| `settings/platform.json` | The platform's choices: address, alerts, admins, size, switches (`variables.tf` in the gateway lists them all) | Platform team |
| `settings/teams/<team>.yaml` | One file per team | Platform team writes it; the team's owner approves |
| `.github/ISSUE_TEMPLATE/team-request.yml` | The request form an app team fills in | Platform team |

## Workflows

| Workflow | When | Does |
|---|---|---|
| `plan.yml` | every pull request | checks the settings against every rule, offline; a broken rule fails it, with every reason |
| `deploy.yml` | merge to main; or by hand (`plan`, `apply`, `settings`, `checks`, `upgrade`, `destroy`) | calls the gateway's own workflow at the pinned release |
| `release-watch.yml` | Mondays | opens a pull request when a newer gateway release is out |
| `rotate.yml` | Mondays | opens a pull request for the next key-rotation step of each team that is due |

## Set up

1. Make this repository from the folder, private. Turn on "Allow GitHub Actions to create and approve pull requests".
2. Run the gateway's `clouds/gcp/scripts/bootstrap.sh` with `GITHUB_REPO=<this repository>` and `GITHUB_ENVIRONMENT=gateway`.
3. Create the environment **gateway** here: only `main`, with reviewers, and the variables the bootstrap prints.
4. Protect `main`: pull requests only, `plan.yml` must pass, and a code owner per team file (`.github/CODEOWNERS`).

## How a team joins

1. The app team opens an issue from **Request access to the LLM gateway**.
2. The platform team writes `settings/teams/<team>.yaml` from it and opens a pull request.
3. The team's owner approves it; `plan.yml` passes; the platform team merges.
4. `deploy.yml` applies the settings and runs the checks as the team.
5. The team's app reads its key from the secret in its own project and calls the one address.
