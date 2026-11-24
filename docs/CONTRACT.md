# The contract

What any cloud and any gateway must provide to be a door in this project. Today one of each is built: Google Cloud
(`clouds/gcp`) and LiteLLM (`gateways/litellm`). Another cloud or gateway is a new folder beside them, and it is a door
when it passes the same checks (`apps/check.py`) and writes the same usage record.

Settings (`settings/`) and the rules that check them (`core/settings`) name no cloud and no gateway unless they must.

## 1. What an app sees

One address. Every request carries the team's key as `Authorization: Bearer <key>`.

| Path | Body | Answer |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI chat format: text messages, the app's own functions as tools | OpenAI chat format, whole or streamed |
| `POST /v1/embeddings` | OpenAI embeddings format, text input | OpenAI embeddings format |
| `GET /v1/models` | none | The team's models only |

The developers' door (docs/DEVELOPERS.md) adds `POST /v1/responses`, `POST /v1beta/models/{model}:streamGenerateContent`
(`?alt=sse` only) and `:generateContent`, and `GET /key/info` (the asking key's own spend). When tool servers are listed,
both doors add `POST /mcp` (JSON-RPC: `jsonrpc`, `id`, `method`, `params`).

Instead of a key, an agent may send a Google ID token for its own service account, with the door's address as audience.
`X-Task-Id` names a task; `X-Acting-For` carries the Google ID token of the person an agent acts for.

Anything else is `404`. Query strings, compressed bodies and fields outside the list are refused before the gateway sees
them.

## 2. Errors

| Code | Meaning | The message contains | An app should |
|---|---|---|---|
| 400 | a field or a shape that is not allowed | `not allowed` | fix the request |
| 400 | too long to be checked whole | `can be checked` | send less |
| 400 | refused by the safety check | `blocked` | show the reason; not retry |
| 401 | unknown or expired key | | check its key |
| 403 | a model the team may not use | | check the model name |
| 422 | the budget is spent | `budget` | stop |
| 429 | too fast, or an agent's task has made its `calls_per_task` | `calls_per_task` for the latter | wait and retry; or start a new task |
| 5xx | the door or a model is failing | | wait and retry |

A gateway may say more, but it must say at least these words, so that checks and apps can rely on them. A refused tool call
is a JSON-RPC error whose message starts `refused:`.

## 3. The safety rule

Every message the caller sends (all roles, tool calls and results, the tools and the response format), embeddings input, and
every answer (text, tool calls and reasoning, whole or streamed) is checked before it goes on. A request too long to be
checked whole is refused. If the check cannot run, or did not run fully, the call is refused.

Proven by `gateways/litellm/gateway/tests/integration` for LiteLLM, against a pretend safety service, on every change.

## 4. What a cloud provides

| Part | Google Cloud (`clouds/gcp`) |
|---|---|
| Runs the gateway in two regions behind one private address, with failover | Cloud Run, cross-region internal load balancer |
| Keeps the gateway's database and its counters | Cloud SQL (PostgreSQL), Memorystore (Redis) |
| Keeps keys and secrets; one team secret per team, readable only by that team's app | Secret Manager |
| Gives each app an identity with no model access of its own | Service accounts |
| Lets the gateway, and nothing else, call models in each team's own account | A custom role with `aiplatform.endpoints.predict` |
| Reaches models and the safety service privately, with no route to the internet | Private Service Connect, Private Google Access |
| Runs the safety check | Model Armor |
| Stores the usage record | Cloud Logging, copied to BigQuery |
| Delivers without stored passwords | GitHub Actions with workload identity federation |

## 5. What a gateway provides

Models with places and failover; teams with models, budgets and limits; keys per team (made by the gateway, written by the
runner to the team's secret); the safety rule above; the usage record below; and an admin interface reachable only from
inside the network.

## 6. The usage record

One line per call, `message: "llm usage"`, field `llm_usage`. Never the question or the answer.

| Field | Since | Meaning |
|---|---|---|
| `schema` | 2 | the record's version |
| `cloud`, `gateway`, `door` | 2 | where it ran: for example `gcp`, `litellm`, `apps` |
| `task` | 2 | the task id from `X-Task-Id`, when given; never used for access |
| `agent`, `acting_for` | 2 | the agent that called, and the person it acted for (from a signed token only) |
| `tool`, `tool_server` | 2 | for a tool-server call: the tool and its server |
| `call_id`, `ok` | 1 | the call, and whether it succeeded |
| `team`, `key`, `name`, `model` | 1 | who, with which key, which of the team's models, which provider model |
| `project`, `location` | 1 | where the model ran and was billed |
| `input_tokens`, `output_tokens`, `cost_usd`, `seconds` | 1 | what it took |
| `error`, `error_code` | 1 | why it failed |

A reader of version 1 ignores the newer fields.

## 7. What the filter tells the gateway

The request filter passes what it established to the gateway as `x-onedoor-task`, `x-onedoor-agent` and
`x-onedoor-acting-for` headers. Callers cannot set them: the filter drops every header it does not know. The usage record
reads them, on every path; no request body is changed.

## 8. Settings versions

`settings/catalogue.yaml` may say `schema: N`. A gateway refuses settings newer than it reads, and names the version it
needs. Every new field has a default that equals the older behaviour, so older settings keep planning unchanged.
