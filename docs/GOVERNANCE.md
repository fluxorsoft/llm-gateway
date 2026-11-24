# Governance

Each item is off unless set, and each is built from Google Cloud and LiteLLM's free parts.

## Safety rules per team

Named rule sets in the catalogue, chosen by a team:

```yaml
# settings/catalogue.yaml
safety:
  strict: {injection: LOW_AND_ABOVE, harmful: LOW_AND_ABOVE, sensitive_data: true}
```
```yaml
# settings/teams/finance.yaml
safety: strict
```

A Model Armor template is made per rule set and region. The guardrail chooses the template for each request from the
caller's team, for questions and answers, streamed or whole; a team without `safety:` uses the platform's. (LiteLLM's own
per-team guardrails are a paid feature.) Levels are `LOW_AND_ABOVE`, `MEDIUM_AND_ABOVE` and `HIGH`.

## Keeping the words, by choice

```yaml
# settings/catalogue.yaml
capture: {approved_teams: [eng], max_days: 90}
```
```yaml
# settings/teams/eng.yaml
capture: {days: 30, readers: ["group:eng-leads@example.com"]}
```

For an approved team only, each question and answer is written to a BigQuery dataset `llm_words` in the team's own project,
deleted after `days`, readable by the named readers. The words never stay in the gateway project's logs. Without approved
teams, nothing is written at all. Before turning it on: tell the people whose words are kept, and ask your legal team.

## The admin record (`audit_record`)

Once a day, what LiteLLM is set to (teams, models, key names and limits; never a key's value) is written to a bucket with a
retention policy: no record can be changed or deleted for `days`. Each record carries the previous one's fingerprint, so a
missing or edited day shows. `lock = true` locks the policy for good: nobody can shorten it, you included.

## A data perimeter (`vpc_sc`)

A VPC Service Controls perimeter around the gateway project and every team project. It needs an organisation and its
access policy (`policy = "accessPolicies/<number>"`). Run the bootstrap again with `VPC_SC_POLICY=accessPolicies/<number>`: the deployer may then edit
perimeters in that policy, and nowhere else. Start with `enforce = false`: violations are logged and nothing is
blocked. Read them in Logging (`protoPayload.metadata.dryRun=true`), add the identities that must still come in (the
GitHub deployer, for example) under `identities`, then set `enforce = true`.

## Your own encryption keys (`cmek_keys`)

A Cloud KMS key per gateway region, for the database and its copy, Redis, the secrets and the usage dataset:

```json
"cmek_keys": {"europe-west2": "projects/p/locations/europe-west2/keyRings/r/cryptoKeys/k", "europe-west1": "..."}
```

Run the bootstrap with `CMEK=1` first, so Google's service agents exist. For new installs: changing a database's key
replaces the database.

## Models near their review date

`review_by` in the catalogue already stops a plan once passed. `model-watch.yml` in the onboarding repository opens an
issue a month before, naming the teams that use the model, with a checklist for trying the replacement with each team's own
examples. `onedoorai models --settings settings` prints the same list.
