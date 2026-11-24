# The developers' door

A second door for coding tools, beside the one for apps. It is the same image and the same settings, run as a second service
(`developer_door` in the platform's settings). It answers as words come, speaks the APIs coding tools use (the Responses API
and the Gemini API), checks every question whole with Model Armor, and refuses what a tool should not send: pictures, files,
and the provider's own tools such as web search.

Proven with Codex CLI 0.172 and Gemini CLI 0.71 against Gemini on Vertex AI (24 November 2026). Claude Code is not part of
1.1.0.

## 1. The platform team turns it on

In `settings/platform.json` (or `PLATFORM_TFVARS`):

```json
"developer_door": {"always_on": "primary", "maximum": 1}
```

The door's address is `https://dev.<domain>` (or `developer_door.domain`). One copy stays running in the primary region; the
other region starts on demand. `always_on: "all"` keeps one in each region; `"none"` starts on demand everywhere, with a wait
of tens of seconds on the first call after a quiet spell.

## 2. The catalogue lists the tools

Coding tools ask for models by their provider's names, and Gemini CLI chooses some of them itself (it used
`gemini-3.8-flash` for every call although it was asked for `gemini-3.5-flash`). The catalogue says which names each tool asks
for:

```yaml
tools:
  gemini-cli: {asks_for: [gemini-3.8-flash, gemini-3.5-flash]}
  codex: {asks_for: []}            # Codex asks for the model named in its own settings
```

## 3. A team file lists its people and tools

```yaml
member_monthly_usd: 30              # each person's share of the team's budget, every month
members:
  - {id: ann.lee@example.com, daily_usd: 5}
  - {id: bob@example.com}
developer_tools: [gemini-cli, codex]
tool_models:                        # the names the tools ask for, each one of this team's models
  gemini-3.8-flash: code
  gemini-3.5-flash: code
```

Each person gets a key of their own, named `<team>.<handle>` (`eng.ann-lee`), in a secret only they can read:
`llm-key-<handle>` in the team's project. The plan refuses a tool whose names are not all mapped, and a name that points
anywhere but the team's own models. (LiteLLM's team aliases are not limited by a key's model list, so these rules are what
keep a team to its own models.)

## 4. The install carries the address

The rule holds when the address comes with the tool. Install each tool with these settings, from the company's software
catalogue or a ready-made development machine.

**Codex CLI**, `~/.codex/config.toml`:

```toml
model = "eng-code"                  # a team model, or one of its tool_models names
model_provider = "door"
web_search = "disabled"             # the provider's web search acts outside the network: the door refuses it

[model_providers.door]
name = "Developers' door"
base_url = "https://dev.llm.internal.example.com/v1"
env_key = "LLM_KEY"
wire_api = "responses"
```

**Gemini CLI**, environment and `~/.gemini/settings.json`:

```bash
export GOOGLE_GEMINI_BASE_URL=https://dev.llm.internal.example.com
```
```json
{"security": {"auth": {"selectedType": "gemini-api-key"}}, "telemetry": {"enabled": false}}
```

**The key, without typing it**, at the start of a session:

```bash
pip install onedoorai
export LLM_KEY=$(onedoorai key --project eng-project)    # Codex
export GEMINI_API_KEY=$LLM_KEY                            # Gemini CLI
```

With the trial's private certificate authority, add its certificate (`terraform output -raw ca_pem`) to the machine's trust
store, or point `NODE_EXTRA_CA_CERTS` (Gemini CLI) at it.

## 5. People see their own spend

```bash
onedoorai spend --door https://dev.llm.internal.example.com --project eng-project
# eng.ann-lee: spent 1.2040 USD of 5.00 a day; resets 2026-11-25T00:00:00+00:00
```

The door answers only about the key that asks. Team leads and finance use the `usage_by_person_month` view (docs/reports).

## What is checked, and what is not

| | Apps' door | Developers' door |
|---|---|---|
| Questions | every message, read whole by Model Armor | the same, including the Responses API's input and instructions and the Gemini API's contents and system instruction |
| Answers | held, and checked whole | not held: words arrive as they come |
| Pictures, files, provider tools | refused | refused |
| Model Armor's basic sensitive-data list | on | on: it includes Google Cloud credentials and keys found in code |
