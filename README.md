# A central LLM gateway on Google Cloud, with LiteLLM

One private REST address that every team in an organisation uses to call language models. A central team decides, per
team, which Google Cloud project pays, which models and locations are allowed, what it may spend, and what happens when
a location is down. Terraform builds it, GitHub Actions delivers it, and checks prove it after every deploy.

It runs [LiteLLM](https://github.com/BerriAI/litellm) on Cloud Run. This project is not affiliated with BerriAI.

![Architecture](docs/images/1-architecture.png)

- [What you get](#what-you-get)
- [New in 1.1.0](#new-in-110)
- [One address, many teams](#one-address-many-teams)
- [How a request is checked](#how-a-request-is-checked)
- [Start](#start)
- [Try it on your machine](#try-it-on-your-machine)
- [Add a team](#add-a-team)
- [Call it from an app](#call-it-from-an-app)
- [When something is down](#when-something-is-down)
- [Run it: keys, upgrades, the admin UI, alerts, cost by team](#run-it)
- [Choose: size, regions, residency, options](#choose)
- [What it costs](#what-it-costs)
- [Security model](#security-model)
- [What was tested, and what was not](#what-was-tested-and-what-was-not)
- [Container images and the Python package](#container-images-and-the-python-package)
- [Licences](#licences)

## What you get

- **One address for every team**, inside your network: a cross-region internal load balancer in front of Cloud Run in
  two regions, with automatic failover.
- **Each team in its own project.** Model calls run in, and are billed to, the team's own Google Cloud project, with its
  own quota.
- **Rules as files.** A model catalogue, and one file per team. A plan that breaks a rule stops and lists every reason.
- **[Model Armor](https://cloud.google.com/security/products/model-armor) on every message and every answer**, streamed or
  not. When it cannot check all of it, the call is refused.
- **No way round it.** A request filter in front of LiteLLM, only the inference paths, default URLs off, no internet
  route, and apps with no model access of their own.
- **Cost by team**, in LiteLLM and in BigQuery, without storing prompts or answers.
- **Keyless delivery** by GitHub Actions, and LiteLLM's image signature verified before every build.

## New in 1.1.0

Nothing changes until it is switched on: a 1.0.0 deployment upgraded with the same settings plans with no change except
the new images, and forgets (never revokes) the keys 1.0.0 made, which the new key step adopts. LiteLLM is 1.110.3.

| | Switch | Read |
|---|---|---|
| A private **onboarding repository** per organisation: settings, request form, plan on pull requests, deploy at a pinned release, release watch, key rotation, model watch | `onboarding/` template | [OPERATIONS](docs/OPERATIONS.md) |
| The **upgrade lane**: a new LiteLLM in one region first, then everywhere | `upgrade` action | [OPERATIONS](docs/OPERATIONS.md) |
| **Keys made by LiteLLM**, written only to the team's secret | `key_maker` | [OPERATIONS](docs/OPERATIONS.md) |
| **Budget alerts**, **cost reports**, a **price check** against the bill, **old spend records** removed, a **database copy** with a failover script, a **chat load test** | `budget_alerts`, `price_check`, `spend_log_days`, `db_replica` | [OPERATIONS](docs/OPERATIONS.md) |
| The **developers' door** for Codex CLI and Gemini CLI, **budgets for each person**, keys fetched with your Google sign-in, your own spend | `developer_door`, `members` | [DEVELOPERS](docs/DEVELOPERS.md) |
| **Safety rules per team**, **keeping the words** by choice, a daily **admin record**, a **data perimeter**, **your own encryption keys**, a **model retirement watch** | `safety`, `capture`, `audit_record`, `vpc_sc`, `cmek_keys` | [GOVERNANCE](docs/GOVERNANCE.md) |
| **Agents** with their own Google identity, budget, limits, stop switch and calls-per-task cap; **task ids**; **tool servers** (MCP) per team and agent; **tool traffic checked** on the way; **provider tools** by request | `agents`, `tool_servers`, `check_tool_traffic`, `provider_tools` | [AGENTS](docs/AGENTS.md) |
| Layers: `core/` names no cloud or gateway; `clouds/gcp/` and `gateways/litellm/` are today's; [the contract](docs/CONTRACT.md) says what another must provide | | [CONTRACT](docs/CONTRACT.md) |
| **onedoorai** on PyPI: the gateway's Python parts, and the `onedoorai` command | `pip install onedoorai` | [below](#container-images-and-the-python-package) |

Found while building and testing it, and fixed here: streamed Gemini calls (Gemini CLI) lost their usage record when the
client closed first (3 of 5 recorded; after the fix, 20 of 20); LiteLLM's Model Armor guardrail ignores tool traffic even
when asked; LiteLLM's team model aliases are not limited by a key's models, so the settings' rules limit them, and its
Gemini route ignores a team's names, so each person's key carries them too; a blank answer (a model that spent its
allowance thinking) gave the caller a 500. The full list of what the live test found is [below](#what-was-tested-and-what-was-not).

The diagrams, ten pages in [docs/litellm-gateway.drawio](docs/litellm-gateway.drawio): architecture, many teams,
configure, a request, failover, **every secret and identity**, onboarding and operations, the developers' door, agents and
tools, governance.

| ![Secrets and identities](docs/images/6-secrets.png) | ![Onboarding and operations](docs/images/7-operations.png) |
|---|---|
| ![The developers' door](docs/images/8-developers.png) | ![Agents and tools](docs/images/9-agents.png) |

### Upgrading from 1.0.0

1. Read the release notes. Move the deploy into an onboarding repository, or keep it here.
2. Run `plan`: only the images change, and the 1.0.0 keys and their secret versions show as "will no longer be managed".
3. Run `apply`. The settings step forgets the keys (`removed`, never destroyed) and the key step adopts them by name.
4. Run `checks`. Then switch on what you want, one switch at a time. Run the bootstrap again first (it is safe to repeat):
   1.1.0's switches need a few more roles for the deployer (keeping words: BigQuery in the team projects).
5. When ready, set `key_maker: litellm` in the catalogue: nothing is revoked.

## One address, many teams

Both example apps call **the same URL, the same path and the same request**. The key decides everything else.

![Many teams](docs/images/2-many-teams.png)

| | Orders team | Support team |
|---|---|---|
| Address | `https://llm.internal.example.com` | the same |
| Request | `POST /v1/chat/completions` | the same |
| Google Cloud project used and billed | its own | its own |
| Key | in a secret in its own project, readable only by its app's service account | the same, in its own project |
| Models | `orders-fast`, `orders-backup` | `support-chat` |
| Residency | `eu` only | `eu`, then `global` (approved in the catalogue) |
| When `eu` is down | another model in `eu`, then an error | the same model in `global` |
| Budget and limits | its own | its own |

### Who may do what (IAM)

Every secret, who writes it and who reads it, and every identity, are on page 6 of the diagrams:

![Secrets and identities](docs/images/6-secrets.png)

| Identity | In the gateway project | In a team's project |
|---|---|---|
| Gateway (`<name>-gateway`), also the developers' door | read its secrets, call Model Armor | **predict only** (`aiplatform.endpoints.predict`), through a custom role |
| Admin service (`<name>-admin`) | read its secrets | nothing: it cannot call a model |
| Runner (`<name>-runner`) | read the master key, call the admin service, its own state bucket; write the agents' map and the admin record; read usage and billing (price check) | write the team's key, and each person's, to their own secrets; disable a removed key's values |
| Scheduler identities (`-scheduler`, `-budgets`, `-pricecheck`, `-audit`) | start the runner with one mode | nothing |
| The team's app (`llm-app`) | nothing | read the team's one secret. **No model access** |
| A member | nothing | read their own `llm-key-<handle>` |
| An agent's identity | nothing: its Google ID token is accepted by the gateway | nothing |
| People in `admin_members` | open the admin service (Cloud Run IAM), then log in to LiteLLM | nothing |
| GitHub Actions (`<name>-github`) | deploy, from one repository (and its `gateway` environment where the plan has environments) | deploy the rows above; with `VPC_SC_POLICY`, edit perimeters in that policy |

An app that calls the model API directly with its own identity gets a 403. A team's key cannot name another team's
model. Two teams' networks are each peered to the gateway's, and not to each other.

## How a request is checked

![Request](docs/images/4-request.png)

**Why a filter in front of LiteLLM.** Before 1.111.0, LiteLLM accepts `vertex_project` and `vertex_location` in a
request. Without the filter, one team's key was served from another team's project, and from the `us` location. The
filter ([guard.py](src/onedoorai/litellm/guard.py)) sees the raw request, allows only known paths, fields and headers, and
passes on a rebuilt body. A LiteLLM hook cannot do this: LiteLLM has already changed the body when the hook sees it.

**Why a guardrail of our own.** LiteLLM's Model Armor guardrail (1.108.15) shows Model Armor only the last block of user
messages, and takes a check that was skipped for a pass. Ours ([armor.py](src/onedoorai/litellm/armor.py)) is a small class on
top of it:

| | Read by Model Armor |
|---|---|
| Sent | every message (system, developer, user, assistant, tool), tool calls, the app's tools, the response format, stop words, embeddings input |
| Answered | the text, the model's reasoning, and tool calls, as one |

The call is refused when Model Armor cannot be reached, when it did not run or skipped any filter, or when the request is
longer than 120,000 characters (`ARMOR_MAX_CHARACTERS`): Model Armor skips its injection check above 65,536 tokens. If a
LiteLLM upgrade changes the class ours stands on, the gateway does not start until someone has looked.

**What a request may hold.** Text, and the app's own functions as tools. Pictures, sound and files are refused, as Model
Armor is shown text here; so are the provider's tools (web search, opening an address, running code), which act from the
provider's side, where this network's walls do not reach.

**Streaming.** Because the whole answer is checked, a streamed answer arrives at once, after the check. That is the
price of checking answers: text already sent cannot be taken back.

## Start

You need a gateway project, one project per team, and an owner of all of them for the bootstrap.

```bash
# 1. once: APIs, the state bucket, the image repository, and a keyless identity for GitHub Actions
PROJECT=my-gateway TEAM_PROJECTS="orders-project support-project" GITHUB_REPO=owner/repo \
  REGION=europe-west2 clouds/gcp/scripts/bootstrap.sh
```

2. In GitHub, create the environment **`gateway`**: allow only the `main` branch, add reviewers, and set these variables
   (the bootstrap prints the first three):

| Variable | Example |
|---|---|
| `GCP_PROJECT`, `GCP_IDENTITY`, `GCP_PROVIDER` | printed by the bootstrap |
| `GCP_REGION` | `europe-west2` |
| `SETTINGS_DIR` | `settings/mycompany` (default `settings/example`) |
| `PLATFORM_TFVARS` | JSON of [clouds/gcp/platform/variables.tf](clouds/gcp/platform/variables.tf): `{"domain": "llm.example.internal", "alert_email": "llm-team@example.com", "admin_members": ["group:llm-admins@example.com"]}` |
| `APP_MEMBERS` | JSON: who may open the example apps |
| `EXAMPLE_PROJECTS` | only with the example settings: `{"orders-project-id": "...", "support-project-id": "..."}` |

3. Run the **deploy** workflow:

| Action | Does |
|---|---|
| `plan` | verifies and builds the images, shows what step 1 would change |
| `apply` | step 1 (Google Cloud), step 2 (LiteLLM's settings), the example apps, then the checks |
| `settings` | a settings change only: the gateways and apps are not redeployed |
| `checks` | runs the checks again, as each team |
| `destroy` | removes everything |

The first apply takes about 20 minutes, most of it Cloud SQL. `destroy` ends with an error about subnets: Cloud Run
keeps their addresses for an hour or two. Everything that costs money is gone by then; run it again later.

> Workflow logs show project ids. Keep the repository you deploy from private.

**Two steps, two states.** Step 1 (`clouds/gcp/platform`) is Google Cloud, applied by GitHub. Step 2
(`gateways/litellm/config`) is LiteLLM's models, teams and keys, through
[LiteLLM's provider](https://github.com/BerriAI/terraform-provider-litellm). It is applied by the **runner**, a Cloud Run
job inside the network, so the admin service is never public and GitHub never holds the master key.

![Configure](docs/images/3-configure.png)

## Try it on your machine

No cloud account, no cost: the published image with pretend models, an admin UI and the request filter.

```bash
cd local && docker compose up -d
# admin UI http://127.0.0.1:4000/ui/ (admin, sk-local-admin); apps call http://127.0.0.1:4001
```

## Add a team

Add `settings/<yours>/teams/<team>.yaml` and open a pull request:

```yaml
owner: support-leads@example.com
project: support-project-id     # where its model calls run and are billed
residency: eu-then-global       # in the catalogue; needs the team's name there
models:
  chat:                         # the app calls it support-chat
    model: gemini-3.5-flash
    locations: [eu, global]     # tried in this order
budget_monthly_usd: 10
limits: {rpm: 60, tpm: 100000}
keys: [1]
key_days: 30
app_service_account: llm-app    # made for the team; the only reader of its key
network: {region: europe-west1, cidr: 10.71.0.0/24}   # leave out when the project already reaches the gateway
```

The plan stops, and lists every reason, when: the model is not in the catalogue or is past its review date; a location is
not offered or is outside the residency; the residency is not approved for the team; a project is used by two teams; the
budget, limits or key life are missing or too high. Then run `bootstrap.sh` for the new project and the `settings`
action.

To remove a team, delete its file: its grant, key, secret and network go.

## Call it from an app

```bash
curl https://llm.internal.example.com/v1/chat/completions \
  -H "Authorization: Bearer $LLM_KEY" -H "Content-Type: application/json" \
  -d '{"model": "support-chat", "messages": [{"role": "user", "content": "Hello"}]}'
```

[apps/llm.py](apps/llm.py) is a whole client in 60 lines of standard-library Python. Any OpenAI-compatible SDK works too:

```python
from openai import OpenAI

client = OpenAI(base_url="https://llm.internal.example.com/v1", api_key=key)
answer = client.chat.completions.create(model="support-chat", messages=[{"role": "user", "content": "Hello"}])
```

Send only standard chat fields, with text messages: others are refused with a 400 that names them. SDKs that add their own fields
(`metadata`, `store`, `extra_body`) must leave them out.

| The app gets | It should |
|---|---|
| 400 `fields not allowed`, `not allowed`, `can be checked` | fix the request |
| 400 `blocked by Model Armor` | show the reason; not retry |
| 401, 403 | check its key and model name; not retry |
| 422 `budget_exceeded` | stop: the month's budget is spent |
| 429 | wait and retry |
| 5xx | wait and retry |
| an error inside a stream | treat the answer as incomplete |

Set the app's time limit above the failover time: the sum of `timeout_seconds` for the locations it may try.

The two example apps ([apps/app.py](apps/app.py)) run in two different projects:

| Order reader (team orders) | Support chat (team support) |
|---|---|
| ![Order reader](docs/images/ui-app-orders.png) | ![Blocked prompt](docs/images/ui-app-blocked.png) |

## When something is down

![Failover](docs/images/5-failover.png)

| | Measured on a real deployment |
|---|---|
| A region's gateway fails its readiness | traffic moved to the other region and back; 1 of 379 calls failed, during a 35-second database restart |
| A release that is not ready | never gets traffic; the old one keeps serving |
| The `eu` model location is cut off | the team with `eu, global` is served from `global`; the `eu`-only team gets an error and never leaves the EU |
| The first call to a dead location | waits for `timeout_seconds` (45), then the location rests for two minutes |
| The alert for it | open about four minutes after the first failure |

**Know these:**

- **There is one database.** While it is down, keys cannot be checked and calls are refused. See
  [a database replica](#a-database-replica).
- **The `eu` location is reached through a private endpoint in an EU region.** A region outside the EU (London) depends on
  the EU one for it. For EU residency that survives a region, use two EU regions, for example `europe-west1` and
  `europe-west4`.
- **The gateway itself processes prompts in its own region**, and so does Model Armor. For strict EU processing, run both
  gateways in the EU.
- **Limits are counted in each region's Redis**, so 60 a minute can allow 120 across two regions. Budgets are in the
  database and written every 10 seconds, so a team can go slightly over.
- **If Redis fails, calls go on** and limits are counted per instance. There is an alert for it.

## Run it

**Rotate a key** with no gap: `keys: [1, 2]`, run `settings`, restart the app (it now reads 2), then `keys: [2]`. Both
work in between; the old one answers 401 at the end. Keys expire after `key_days`: rotate before that.

**Open the admin UI.** Give people access with `admin_members`, then, from a machine that can reach it
(`admin_public_ingress` allows that from outside the network; IAM still applies):

```bash
pip install onedoorai
onedoorai admin --url "$(terraform -chdir=clouds/gcp/platform output -raw admin_url)"
# http://127.0.0.1:9000/ui/  user: admin  password: the <name>-master-key secret
```

Look, and change settings in Git: the daily drift check (`drift_check`) finds changes made by hand, alerts, and the next
apply puts them back. For production, make a named admin in LiteLLM and set
`general_settings.disable_env_credential_login: true`, so the master key is not a login.

| Team | One request |
|---|---|
| ![Team](docs/images/ui-team-detail.png) | ![Request](docs/images/ui-log-detail.png) |

**Cost by team** in BigQuery (one line per call; no prompts):

```sql
select jsonPayload.llm_usage.team, jsonPayload.llm_usage.project, jsonPayload.llm_usage.location,
       count(*) calls, round(sum(jsonPayload.llm_usage.cost_usd), 2) usd
from `my-gateway.llmgw_usage.run_googleapis_com_stdout`
where jsonPayload.llm_usage.ok group by 1, 2, 3 order by usd desc
```

**Alerts:** models failing in a location; Redis failing; Model Armor failing; requests with fields that are not allowed;
the settings run failing or finding changes; gateway 5xx; the database down.

**Upgrade LiteLLM:** read what changed in its Model Armor guardrail, then put a new digest in [LITELLM_IMAGE](gateways/litellm/LITELLM_IMAGE) and the same version in
[gateways/litellm/config](gateways/litellm/config/main.tf), then run the `upgrade` action: one region first (docs/OPERATIONS.md). The signature is verified, the migration runs first, and an
unready revision gets no traffic. Pin digests of the LTS line, and let a release age about a week unless it fixes a
security fault: on 21 October 2026 the `latest` and `main-stable` tags briefly carried malware. Roll back by reverting the commit, if the
migration allows it: take a database backup first.

**Turn a team off now:** block its key or team in the admin UI, then remove its file.

## Choose

| Setting | Trial (the default) | Production |
|---|---|---|
| `size.db_tier`, `db_connections` | `db-g1-small`, 50 | `db-custom-2-7680`, 400 |
| `size.db_regional` | false | true: survives a zone |
| `size.redis_ha` | false | true |
| `size.gateway_maximum` | 3 | by load: each instance uses 5 database connections; the plan checks the sum |
| `certificate_ids` | none: a private CA is made (its key is in the Terraform state) | your Certificate Manager certificates, scope `ALL_REGIONS` |
| `deletion_protection` | set false for a trial | true (the default) |
| `drift_check`, `alert_email` | off | on |
| `regions` | two | two; both in the EU for EU residency |

### A database replica

`db_replica` makes a copy in the second region; `clouds/gcp/scripts/db_failover.sh` promotes it and points
`db.<name>.internal` at it; `db_failback.sh` brings the data home and the next apply makes a new copy; plans refuse in
between, so an apply cannot replace the live database (docs/OPERATIONS.md). You run the failover yourself, and recent
writes can be lost (replication is asynchronous). Enterprise Plus adds managed switchover and a write endpoint, at
about four times the extra price.

### Single sign-on

LiteLLM's own single sign-on is free for up to five users and an Enterprise feature beyond. Without it: Cloud Run IAM
already decides who reaches the admin service; Identity-Aware Proxy can put your identity provider in front of it; or a
small portal of your own can call LiteLLM's admin API for self-service. Teams do not need the UI: they change files.

### Others

Other model providers (they need a controlled way out, as there is no internet route), other clouds and other gateways
(the layout and [the contract](docs/CONTRACT.md) leave room), caching, and agents calling agents. Safety rules per team, a
record of admin changes, your own keys, keeping words, MCP and agents are in 1.1.0, built without LiteLLM's paid parts.

## What it costs

List prices in GBP, November 2026, London and Belgium, 730 hours, before tokens:

| | Trial | Production |
|---|---|---|
| Two gateways (1 vCPU, 4 GiB, always on) | 113 | 113 |
| Cloud SQL | 25 | 184 |
| Redis, two regions (1 GB; with replicas in production) | 57 | 97 |
| Load balancer, two regions | 58 | 58 |
| Private endpoints, admin service, logs, secrets, DNS (estimate) | 25 | 30 |
| **A month** | **about 280** | **about 480** |

Tokens are usually the larger part: 100,000 calls of 1,000 tokens in and 300 out on Gemini 3.5 Flash in `eu` are about
350. Model Armor's first 2 million tokens a month are free, then about 0.08 per million.

## Security model

- **No internet route.** No NAT; Google is reached through Private Google Access, and private endpoints for the `eu`
  location and Model Armor. A compromised dependency has nowhere to send anything.
- **Predict only.** The gateway's identity can call models in team projects and do nothing else there. It can do so in
  every team's project: protect it, and keep the filter.
- **Keys are never in Terraform state or plans**: made by LiteLLM (`key_maker: litellm`) or in memory, written only to
  secrets by the key step; the database's password is sent with a write-only argument and never kept. Redis's password, and
  the trial CA's private key, are in the state; its bucket is private and versioned.
- **The image** is verified (cosign, keyless), pinned by digest, runs as a non-root user, and holds only LiteLLM's open
  source parts.
- **The salt key must never change**: it encrypts what LiteLLM keeps in the database.
- **Apps that could go round** must not hold `aiplatform` roles in their projects; `vpc_sc` puts a VPC Service Controls
  perimeter round every project (measured: enforced, every check passed).
- **Model Armor lowers the risk of a trick; it does not remove it.** Give apps and their tools only what they need.
- **GitHub Actions** can deploy to all these projects. Its identity is accepted only from one repository's `gateway`
  environment (protect it with reviewers), or, on a plan without environments, only from `main`.

Report a fault privately through GitHub's security advisories.

**1.0.0 was rebuilt on 22 November 2026**, hours after it first appeared. In the first build (gateway image
`sha256:844ee672ac10...`) Model Armor read only the last block of user messages: system messages, earlier turns, tool
results and embeddings input were not read, and neither was a model's reasoning. A check that was skipped, as for a very
long text, counted as a pass, and a provider's tools were let through. We found this ourselves. If you pulled 1.0.0 that
day, pull it again, and compare the digest.

## What was tested, and what was not

**1.1.0, live, 24 and 25 November 2026**, trial size, two regions, two teams in their own projects, deployed from a private
onboarding repository on GitHub Free. Every switch was turned on and proven, and each run of each team's checks passed:

| | Measured |
|---|---|
| Upgrading a running 1.0.0 | `plan`: 0 to add, 5 to change (the images only), 0 to destroy; the 1.0.0 keys adopted, none revoked; plan again: no changes |
| Database copy | failover about 6 min, fail-back about 6 min, a new copy 7.5 min; checks passed on the promoted copy and after |
| Upgrade lane | one region about 5.5 min, the rest 3 min; a gateway that cannot start stopped it at the first region, traffic stayed on the last good revision |
| Key rotation | by pull request, twice; the app restarted onto the new key; a removed key's secret values disabled |
| Load (apps' door) | 72 of 72 at 0.8 a second: whole answer p50 1075 ms, p95 1420 ms; first streamed words p50 1168 ms, p95 1494 ms |
| Developers' door | first words 2.2 to 3.8 s; a region starting on demand 71 s for the first call, then 1.9 s; a person at their limit 422, the other carries on; 6 of 6 streamed Gemini calls recorded |
| Agents | identity token accepted, a stranger refused; the 4th call of a task refused; stop switch 2 s and 27 s in the two regions |
| Tools | the team's two tools, the agent's one; an injection in an argument and in an answer refused; about 0.36 s a tool call |
| Governance | a stricter team refused what the other may send; words kept only for the approved team; two admin records chained, a delete refused; the perimeter enforced with every check passing; a fresh install on your own keys |
| Alerts | a budget alert at 100 per cent; a price check drift alert (43.5 per cent, against a stand-in bill) |

Found by the live test, and fixed before the release (each with a test where one can be written):
- an apply after a database failover would have replaced the promoted copy, the live database: plans now refuse it, and
  `db_failback.sh` brings the data home;
- the deploy restarted only apps whose keys changed in the last commit of a push; a deploy step could pass with no checks
  run when a command at the head of a pipe failed;
- the developers' door: a backend timeout and a certificate change Google refuses; `/key/info` and `/mcp` missing from the
  load balancers' path lists; every Gemini API call refused (a person's key now carries the tools' names);
- team keys saw no tools; the calls-per-task cap counted a tool session's set-up; the usage record never named the tool;
- a tool server's name with `-` stopped every gateway at start (now refused by the settings' rules);
- keeping words made the team's dataset in US: now the gateway's primary region;
- a fresh install with your own keys made its secrets before the key could be used (the agents' map secret did not use
  the key at all); a first install that failed partway could leave the database's password unknown (`db_password_round`
  renews it);
- `release-watch.yml` looked for GitHub Releases (the gateway has tags) and could propose an older release; it now opens an
  issue, or a pull request with a token. `rotate.yml` checks the settings itself (GitHub holds checks on a pull request
  made by Actions); `rotate --now` for a key that leaked;
- the bootstrap now gives the deployer BigQuery in team projects (keeping words) and, with `VPC_SC_POLICY`, the perimeter.

Known, and documented: the first usage record carrying a newly used field can reach Cloud Logging but not BigQuery while
BigQuery takes the new column; cancelling a deploy while it destroys can leave a Terraform lock (remove it only if no other
run holds it).

**The first build, on a real deployment**, trial size, 22 November 2026, LiteLLM 1.108.15: 29 checks as each of two
teams; both failovers; drift; key rotation; the alerts for models and for settings; BigQuery usage; 600 calls from one
client at 114 a second (`GET /v1/models`, median 165 ms, no errors); teardown.

**1.0.0 as it is now, every change** ([ci.yml](.github/workflows/ci.yml)): the image as it ships, with the real LiteLLM,
pretend models and a pretend Model Armor: what was shown to Model Armor, forbidden words in every place a request can
hold them, answers streamed and whole, skipped and failed checks, long requests. The same tests fail on the first
build's setup.

**1.0.0 as it is now, the real Model Armor** in `europe-west2` and `europe-west1`: normal questions and answers count as
a full check; 120,000 characters, in English and in Japanese, are checked whole; 300,000 come back as skipped, and are
refused; an injection after 119,000 harmless characters is found.

**1.0.0 as it is now, deployed whole** on 22 November 2026 from the published images (the runner with our settings
added), trial size, two regions: all 35 checks ([apps/check.py](apps/check.py)) as each of the two teams, with real
models and the real Model Armor; a streamed answer with a card number is blocked, and a clean one passes; the settings
plan finds no change. The failover, drift, rotation, alert and load tests above ran on the first build, and were not
repeated.

Not tested: the loss of a whole Google Cloud region, production sizes, sustained load, your own certificates, Shared VPC
and on-premises callers, embeddings with a real model, and the drift schedule (its job was run by hand).

## Container images and the Python package

| Image | For |
|---|---|
| `ghcr.io/fluxorsoft/llm-gateway/gateway` | the gateway and the admin service |
| `ghcr.io/fluxorsoft/llm-gateway/runner` | applies the settings; add yours: `FROM` it, `COPY settings /repo/settings/current` |
| `ghcr.io/fluxorsoft/llm-gateway/app` | the example apps and the checks |

They are signed. Verify with cosign 3 or newer:

```bash
cosign verify ghcr.io/fluxorsoft/llm-gateway/gateway:1.1.0 \
  --certificate-identity-regexp '^https://github.com/fluxorsoft/llm-gateway/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The Python parts are on PyPI as [onedoorai](https://pypi.org/project/onedoorai/) (`pip install onedoorai`;
`onedoorai[litellm]` for the parts that run inside a LiteLLM proxy): `onedoorai admin`, `key`, `spend`, `rotate`, `models`.

Cloud Run cannot pull from GHCR directly: copy them by digest to
your Artifact Registry, or let the workflow build its own.

## Licences

This repository is [Apache 2.0](LICENSE). LiteLLM is MIT, except its `enterprise` folder, whose licence does not allow
production use or distribution without an agreement with BerriAI. The gateway does not use it, so
[the image](gateways/litellm/gateway/Dockerfile) leaves it out. The runner image includes Terraform (Business Source License 1.1).
"LiteLLM" is BerriAI's name, used here to say what this runs.
