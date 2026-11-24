# Running it day to day

Everything here is a switch in the platform's settings (`settings/platform.json` in an onboarding repository, or
`PLATFORM_TFVARS`), off unless set. The names are the variables in
[clouds/gcp/platform/variables.tf](../clouds/gcp/platform/variables.tf).

## The onboarding repository

The gateway's code and an organisation's settings live apart. Make a **private** repository from
[onboarding/](../onboarding/): it pins a gateway release, holds the catalogue, the platform's settings and one file per
team, and runs the gateway's own deploy workflow at that release. Its workflows:

| Workflow | When | Does |
|---|---|---|
| `plan.yml` | every pull request | checks the settings against every rule, offline |
| `deploy.yml` | merge to main, or by hand | `plan`, `apply`, `settings`, `checks`, `upgrade`, `destroy` |
| `release-watch.yml` | Mondays | a pull request when a newer gateway release is out |
| `rotate.yml` | Mondays | a pull request for each team's next key-rotation step |
| `model-watch.yml` | Mondays | an issue a month before a model's review date |

Keeping settings inside this repository, as 1.0.0 did, still works.

## Upgrading LiteLLM: the lane

Put a new digest in [gateways/litellm/LITELLM_IMAGE](../gateways/litellm/LITELLM_IMAGE) and run the deploy workflow with
`upgrade`. The migration and the admin service take the new image, then one region's gateway (the first region that is not
the primary one). The runner checks that LiteLLM still accepts the way keys are made (`MODE=probe`), applies the settings,
and the teams served there run their checks. Only then does every region move, and every team's checks run. A failure
stops the lane with the other regions untouched. While both images run, the second region costs one more instance.

## Keys

Keys are made by the key step in the runner (`onedoorai keys`), never by Terraform: a key's value is written to the team's
secret and nowhere else. `key_maker` in the catalogue chooses who picks the value: `litellm` (the default for settings
schema 2) or `terraform` (1.0.0's way, the default for older settings, with a warning on every plan). LiteLLM has announced
that it will stop accepting chosen values; the upgrade lane refuses a LiteLLM that has, until you switch. Switching revokes
nothing.

Key rotation by pull request: `rotate.yml` in the onboarding repository adds the next key when one is within 7 days of `key_days`, the deploy restarts
the services listed under `restart:` in the team file, and the next run removes the old key.

## Budget alerts (`budget_alerts`)

Every hour the runner writes each team's spend against its budget; an alert per team tells its `owner`, and the platform's
`alert_email`, at 80 and 100 per cent, at most once a day while it lasts.

## Cost reports

The deploy makes these views in BigQuery after its checks ([docs/reports](reports/)): by team and day, team and month,
model and place, task, person, and failures by kind. They never hold questions or answers. Connect Looker Studio, or
anything that reads BigQuery.

## Price check (`price_check`)

Every Monday, last week's gateway cost per team project against the Vertex AI lines of the Cloud Billing export, with an
alert when they differ by more than 10 per cent. A billing administrator turns the export on; give its table as
`price_check.billing_export`. The bill also counts Vertex AI use that does not pass the gateway (RAG Engine's own
embeddings, for example): such a team always differs, and the alert is for a change.

## Old spend records (`spend_log_days`)

LiteLLM's own retention deletes spend rows older than this many days. The usage record in BigQuery keeps everything.

## A database copy (`db_replica`)

A copy in the second region, kept up to date. Save the failover details now, while all is well:

```bash
terraform -chdir=clouds/gcp/platform output -json db_failover > db_failover.json
```

To make the copy the database, when the primary region is in trouble:

```bash
clouds/gcp/scripts/db_failover.sh < db_failover.json
```

It promotes the copy, points the database's private name at it, and restarts the gateways. Recent writes not yet copied
can be lost (a few seconds of spend). From then on every plan refuses: an apply would replace the promoted copy, which is
now the live database, with a new copy of the old one.

To come back, when the primary region is well again:

```bash
clouds/gcp/scripts/db_failback.sh < db_failover.json
```

It copies the data from the promoted copy into the primary database (an export and an import through a short-lived
bucket), points the name back at the primary, restarts the gateways, and deletes the promoted copy and the bucket. Writes
between the export and the restart are lost: a few seconds of spend. Run the checks, then `apply`: it makes a new copy.

Measured on trial size: the failover took about 6 minutes, nearly all of it the promotion; the fail-back about 6
minutes; the new copy NEWCOPY. The scripts need `gcloud`, and `jq` or Python.

## A load test

`CHECKS=chat-load` on a team's check job: real chat calls, whole and streamed, at `LOAD_PER_SECOND` for `LOAD_SECONDS`,
with the time to a whole answer and to the first streamed words.

## Limits across regions

Each region counts limits in its own Redis, so 60 a minute can allow up to 120 across two regions. Measured: one shared
Redis counts exactly, and adds about 7 ms a Redis call across regions. Not offered as a switch in 1.1.0.
