# Agents and tools

An agent decides its own next step and makes many calls. The gateway is where you see it and stop it. What a tool may do
is a separate lock: it stays with the tool.

## An agent in a team file

```yaml
agents:
  - name: triage
    identity: triage@team-project.iam.gserviceaccount.com   # the agent's own service account
    daily_usd: 3
    rpm: 30
    calls_per_task: 40          # optional: then every call must carry X-Task-Id
    tools: {files: [read]}      # tool servers and tools, within the team's
    stopped: false              # true blocks its key at once; false starts it again
```

Each agent has its own key (`<team>~<name>`), its own budget and limits, and appears in the usage record by name.

## Signing in with its Google identity

An agent sends a Google ID token for its service account, with the gateway's address as audience, instead of a key:

```python
import google.auth.transport.requests, google.oauth2.id_token
token = google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), "https://llm.internal.example.com")
# Authorization: Bearer <token>
```

The request filter verifies it (Google's signature, issuer, audience, expiry), finds the agent in a map only the gateway
reads, and passes on the agent's own key, so LiteLLM checks its budget and limits as for any key. An identity that is not
an agent of any team is refused. LiteLLM's paid JWT sign-in is not used. Agents with a key also work.

## A task, and acting for a person

`X-Task-Id: <id>` (up to 64 letters, digits and `. _ : -`) is kept in the usage record, so a task's cost is one line
(`usage_by_task`). `calls_per_task` counts an agent's model calls and tool calls per task for a day in the region's Redis (a
tool session's set-up, `initialize` and `tools/list`, is not counted and needs no task); the next call gets 429. `X-Acting-For: <Google ID token of the person>` records the person, only from a signed token.

## Tool servers (MCP)

```yaml
# settings/catalogue.yaml
tool_servers:
  files: {url: "https://files.internal.example.com/mcp"}
```
```yaml
# settings/teams/eng.yaml
tool_servers: {files: [read, list]}     # or ["*"] for every tool
```

Servers are listed in the gateway's config; a team may use the servers and tools in its file; each agent only those in its
`tools`. A key without a grant sees no server (`require_key_mcp_access_defined` is always on: without it LiteLLM gives a key
every server of its team). The gateway never passes a caller's credentials to a tool server. The usage record names the
tool and the server.

## Checking tool traffic on the way (`check_tool_traffic`)

Model Armor reads each tool call's name and arguments before the call goes on, and the tool's answer is held and read
before it comes back. Blocked, not fully checked, or not checkable: the call is refused with a tool error. About 0.15 s a
check. Off: tool answers are read when they come back in the next model call.

This is done by the request filter because LiteLLM's Model Armor guardrail does not run on tool traffic, even with
`pre_mcp_call` and `post_mcp_call` set: it accepts the setting and does nothing (LiteLLM 1.110.3). Tool traffic uses the
platform's Model Armor rules, not a team's own `safety:` set.

## The provider's own tools

Web search and running code act outside the network. They are refused, unless the catalogue lists one with a reason, a
residency note and a price, and a team asks for it:

```yaml
# settings/catalogue.yaml
provider_tools:
  google_search: {reason: "...", residency: "may be processed outside the EU", price: "per grounded prompt"}
```
```yaml
# settings/teams/news.yaml
provider_tools: [google_search]
```

Every other team is still refused. Names: `google_search`, `url_context`, `code_execution`.

## Not in 1.1.0

Agents calling agents (A2A). Each agent still calls the gateway with its own identity, so it is counted.
