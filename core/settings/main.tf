# Reads the catalogue and the team files, checks them, and hands both Terraform steps the same answer. A broken rule
# stops the plan, with every broken rule listed.
terraform {
  required_version = ">= 1.11"
}

variable "dir" {
  type        = string
  description = "Folder with catalogue.yaml and teams/*.yaml"
}

locals {
  catalogue = yamldecode(file("${var.dir}/catalogue.yaml"))
  teams     = { for f in fileset("${var.dir}/teams", "*.yaml") : trimsuffix(f, ".yaml") => yamldecode(file("${var.dir}/teams/${f}")) }
  required  = ["owner", "project", "residency", "models", "budget_monthly_usd", "limits", "keys", "key_days", "app_service_account"]

  # The settings name no cloud and no gateway unless they must. Every field below defaults to what 1.0.0 did, so its files
  # plan unchanged. A value this version cannot serve yet is refused here, with the reason, rather than half-applied.
  schema    = try(local.catalogue.schema, 1)
  readable  = 2 # the newest settings schema this version reads
  clouds    = ["gcp"]
  providers = ["vertex"]

  # people with a personal key (the developers' door): a handle from the address names their key and their secret
  members = { for t, v in local.whole : t => [for m in try(v.members, []) : {
    id        = try(m.id, "")
    handle    = substr(trim(replace(lower(split("@", try(m.id, ""))[0]), "/[^a-z0-9]+/", "-"), "-"), 0, 30)
    daily_usd = try(m.daily_usd, null)
  }] }

  # coding tools ask for models by their provider's names; a team maps each name to one of its own models. LiteLLM's team
  # aliases are not limited by the key's model list, so the rules below are what keep a team to its own models.
  tools          = try(local.catalogue.tools, {})
  all_model_keys = [for k, m in local.models : "${m.team}-${m.alias}"]

  # safety rules per team: named rule sets in the catalogue (Model Armor confidence levels), chosen by a team's `safety:`
  safety = try(local.catalogue.safety, {})
  levels = ["LOW_AND_ABOVE", "MEDIUM_AND_ABOVE", "HIGH"]

  # keeping the words: only for teams the catalogue approves, for at most its max_days
  capture_ok   = try(local.catalogue.capture.approved_teams, [])
  capture_days = try(local.catalogue.capture.max_days, 90)

  # agents: a named entry with its own Google identity, budget and limits; stopped blocks its key at once
  agents = { for t, v in local.whole : t => [for g in try(v.agents, []) : {
    name      = try(g.name, "")
    identity  = try(g.identity, "")
    owner     = try(g.owner, v.owner)
    daily_usd = try(g.daily_usd, null)
    rpm       = try(g.rpm, null)
    stopped   = try(g.stopped, false)
    tools     = try(g.tools, {}) # {server: [tool]}: within the team's tool_servers
  }] }
  # tool servers (MCP): listed in the catalogue, allowed per team with the tools it may call ("*" for all)
  servers          = try(local.catalogue.tool_servers, {})
  team_servers     = { for t, v in local.whole : t => try(v.tool_servers, {}) }
  agent_identities = flatten([for t, gs in local.agents : [for g in gs : g.identity]])

  # Who chooses a key's value: LiteLLM (schema 2 and later), or we do, as 1.0.0 did (schema 1, until switched).
  key_maker = try(local.catalogue.key_maker, local.schema >= 2 ? "litellm" : "terraform")

  # a file with a missing field is reported once, and left out of the other checks
  whole = { for t, v in local.teams : t => v if alltrue([for f in local.required : contains(keys(v), f)]) }

  models = merge([for t, v in local.whole : { for a, m in v.models : "${t}/${a}" => {
    team      = t
    alias     = a
    residency = v.residency
    model     = m.model
    locations = m.locations
    fallback  = try(m.fallback, null)
  } }]...)

  errors = concat(
    contains(["litellm", "terraform"], local.key_maker) ? [] : ["catalogue: key_maker is litellm or terraform, not '${local.key_maker}'"],
    local.schema > local.readable ? ["catalogue: settings schema ${local.schema} needs a newer gateway; this version reads up to ${local.readable}"] : [],
    [for t, v in local.whole : "${t}: cloud '${try(v.cloud, "gcp")}' is not available in this version (available: ${join(", ", local.clouds)})" if !contains(local.clouds, try(v.cloud, "gcp"))],
    [for m, c in local.catalogue.models : "catalogue: model '${m}' has provider '${try(c.provider, "vertex")}', not available in this version (available: ${join(", ", local.providers)})" if !contains(local.providers, try(c.provider, "vertex"))],
    [for t, v in local.teams : "${t}: missing ${join(", ", setsubtract(local.required, keys(v)))}" if !contains(keys(local.whole), t)],
    [for t in keys(local.teams) : "${t}: a team name is 2 to 20 lower-case letters, digits and hyphens" if !can(regex("^[a-z][a-z0-9-]{1,19}$", t))],
    [for t, v in local.whole : "${t}: residency '${v.residency}' is not in the catalogue" if !contains(keys(local.catalogue.residency), v.residency)],
    [for t, v in local.whole : "${t}: residency '${v.residency}' needs the team in approved_teams" if !contains(try(local.catalogue.residency[v.residency].approved_teams, [t]), t)],
    [for k, m in local.models : "${k}: model '${m.model}' is not in the catalogue" if !contains(keys(local.catalogue.models), m.model)],
    [for k, m in local.models : "${k}: model '${m.model}' is past its review date" if contains(keys(local.catalogue.models), m.model) && try(timecmp(plantimestamp(), "${local.catalogue.models[m.model].review_by}T00:00:00Z") > 0, true)],
    [for k, m in local.models : "${k}: no location" if length(m.locations) == 0],
    flatten([for k, m in local.models : [for l in m.locations : "${k}: model '${m.model}' is not offered in '${l}'" if !contains(try(local.catalogue.models[m.model].locations, [l]), l)]]),
    flatten([for k, m in local.models : [for l in m.locations : "${k}: location '${l}' is outside residency '${m.residency}'" if !contains(try(local.catalogue.residency[m.residency].locations, [l]), l)]]),
    [for k, m in local.models : "${k}: fallback '${m.fallback}' is not another model of the team" if m.fallback != null && (m.fallback == m.alias || !contains(keys(local.models), "${m.team}/${coalesce(m.fallback, "-")}"))],
    [for t, v in local.whole : "${t}: the budget must be above 0 and at most ${local.catalogue.limits.max_monthly_usd} USD a month" if !try(v.budget_monthly_usd > 0 && v.budget_monthly_usd <= local.catalogue.limits.max_monthly_usd, false)],
    [for t, v in local.whole : "${t}: limits need rpm and tpm above 0" if !try(v.limits.rpm > 0 && v.limits.tpm > 0, false)],
    [for t, v in local.whole : "${t}: key_days must be between 1 and ${local.catalogue.limits.max_key_days}" if !try(v.key_days >= 1 && v.key_days <= local.catalogue.limits.max_key_days, false)],
    [for t, v in local.whole : "${t}: keys must be one or more different whole numbers" if !try(length(v.keys) > 0 && length(distinct(v.keys)) == length(v.keys) && alltrue([for k in v.keys : k >= 1 && floor(k) == k]), false)],
    flatten([for t, ms in local.members : [for m in ms : "${t}: a member's id is an email address, not '${m.id}'" if !can(regex("^[^@\\s]+@[^@\\s]+$", m.id))]]),
    [for t, ms in local.members : "${t}: two members have the same name before the @" if length(distinct([for m in ms : m.handle])) != length(ms)],
    flatten([for t, ms in local.members : [for m in ms : "${t}: ${m.id}'s daily_usd must be above 0 and at most the team's budget" if m.daily_usd != null && !try(m.daily_usd > 0 && m.daily_usd <= local.whole[t].budget_monthly_usd, false)]]),
    [for t, v in local.whole : "${t}: member_monthly_usd must be above 0 and at most the team's budget" if try(v.member_monthly_usd, null) != null && !try(v.member_monthly_usd > 0 && v.member_monthly_usd <= v.budget_monthly_usd, false)],
    flatten([for t, v in local.whole : [for x in try(v.developer_tools, []) : "${t}: tool '${x}' is not in the catalogue" if !contains(keys(local.tools), x)]]),
    flatten([for t, v in local.whole : [for n, a in try(v.tool_models, {}) : "${t}: tool_models '${n}' points at '${a}', which is not one of the team's models" if !contains(keys(v.models), a)]]),
    flatten([for t, v in local.whole : [for n, a in try(v.tool_models, {}) : "${t}: tool_models '${n}' is also the name of a model here" if contains(local.all_model_keys, n)]]),
    flatten([for t, v in local.whole : [for x in try(v.developer_tools, []) : [for n in try(local.tools[x].asks_for, []) : "${t}: tool '${x}' asks for '${n}': add it to tool_models" if !contains(keys(try(v.tool_models, {})), n)]]]),
    [for t, v in local.whole : "${t}: safety '${v.safety}' is not in the catalogue" if try(v.safety, null) != null && !contains(keys(local.safety), try(v.safety, ""))],
    flatten([for n, r in local.safety : [for k, l in r : "catalogue: safety '${n}': ${k} is one of ${join(", ", local.levels)}" if contains(["injection", "harmful"], k) && !contains(local.levels, l)]]),
    flatten([for n, r in local.safety : [for k in keys(r) : "catalogue: safety '${n}' sets '${k}'; it can set injection, harmful and sensitive_data" if !contains(["injection", "harmful", "sensitive_data"], k)]]),
    [for t, v in local.whole : "${t}: keeping words needs the team in the catalogue's capture.approved_teams" if try(v.capture, null) != null && !contains(local.capture_ok, t)],
    [for t, v in local.whole : "${t}: capture.days must be 1 to ${local.capture_days}" if try(v.capture, null) != null && !try(v.capture.days >= 1 && v.capture.days <= local.capture_days, false)],
    flatten([for t, v in local.whole : [for r in try(v.capture.readers, []) : "${t}: a capture reader is user:, group: or serviceAccount:, not '${r}'" if !can(regex("^(user|group|serviceAccount):", r))]]),
    flatten([for t, gs in local.agents : [for g in gs : "${t}: an agent's name is 2 to 30 lower-case letters, digits and hyphens, not '${g.name}'" if !can(regex("^[a-z][a-z0-9-]{1,29}$", g.name))]]),
    flatten([for t, gs in local.agents : [for g in gs : "${t}: agent '${g.name}' needs its own service account as identity, not '${g.identity}'" if !can(regex("^[a-z0-9-]+@[a-z0-9-]+\\.iam\\.gserviceaccount\\.com$", g.identity))]]),
    [for i in distinct(local.agent_identities) : "identity '${i}' is used by more than one agent" if length([for x in local.agent_identities : x if x == i]) > 1],
    flatten([for t, gs in local.agents : [for g in gs : "${t}: agent '${g.name}' daily_usd must be above 0 and at most the team's budget" if g.daily_usd != null && !try(g.daily_usd > 0 && g.daily_usd <= local.whole[t].budget_monthly_usd, false)]]),
    flatten([for t, ss in local.team_servers : [for s, _ in ss : "${t}: tool server '${s}' is not in the catalogue" if !contains(keys(local.servers), s)]]),
    flatten([for n, s in local.servers : "catalogue: tool server '${n}' needs an https:// url inside the network" if !can(regex("^https?://", s.url))]),
    flatten([for t, gs in local.agents : [for g in gs : [for s, tools in g.tools : "${t}: agent '${g.name}' uses tool server '${s}', which the team may not use" if !contains(keys(local.team_servers[t]), s)]]]),
    flatten([for t, gs in local.agents : [for g in gs : [for s, tools in g.tools : [for x in tools : "${t}: agent '${g.name}' uses '${s}/${x}', which the team may not use" if contains(keys(local.team_servers[t]), s) && !contains(local.team_servers[t][s], "*") && !contains(local.team_servers[t][s], x)]]]]),
    [for p in distinct([for t, v in local.whole : v.project]) : "project '${p}' is used by more than one team" if length([for t, v in local.whole : t if v.project == p]) > 1],
  )
}

output "teams" {
  value = { for t, v in local.whole : t => merge(v, { cloud = try(v.cloud, "gcp"), members = local.members[t], agents = local.agents[t] }) }
  precondition {
    condition     = length(local.errors) == 0
    error_message = "The settings break these rules:\n- ${join("\n- ", local.errors)}"
  }
}

output "errors" { value = local.errors }

output "schema" { value = local.schema }

output "key_maker" { value = local.key_maker }

# the rule sets teams use: {name: {injection, harmful, sensitive_data}}, and which team uses which
output "safety" {
  value = { for n, r in local.safety : n => {
    injection      = try(r.injection, "MEDIUM_AND_ABOVE")
    harmful        = try(r.harmful, "MEDIUM_AND_ABOVE")
    sensitive_data = try(r.sensitive_data, true)
  } if contains([for t, v in local.whole : try(v.safety, "")], n) }
}

output "capture" {
  value = { for t, v in local.whole : t => { days = v.capture.days, readers = try(v.capture.readers, []) } if try(v.capture, null) != null && contains(local.capture_ok, t) }
}

# the tool servers some team uses, for the gateway's config
output "tool_servers" {
  value = { for n, s in local.servers : n => { url = s.url, transport = try(s.transport, "http") } if anytrue([for t, ss in local.team_servers : contains(keys(ss), n)]) }
}

output "agents_on" { value = length(local.agent_identities) > 0 }

output "team_safety" { value = { for t, v in local.whole : t => v.safety if try(v.safety, null) != null } }

# LiteLLM will stop accepting key values chosen by the caller; until a deployment switches, every plan says so.
check "key_maker" {
  assert {
    condition     = local.key_maker == "litellm"
    error_message = "Keys are still made the 1.0.0 way (we choose the value). LiteLLM will stop accepting that: set key_maker: litellm in the catalogue. Nothing is revoked when you switch."
  }
}

# one per team, model and location; order is the failover order
output "deployments" {
  value = merge([for k, m in local.models : { for i, l in m.locations : "${k}/${l}" => {
    name     = "${m.team}-${m.alias}"
    team     = m.team
    model    = m.model
    location = l
    order    = i + 1
    project  = local.whole[m.team].project
    timeout  = try(local.catalogue.models[m.model].timeout_seconds, 45)
  } }]...)
  precondition {
    condition     = length(local.errors) == 0
    error_message = "The settings break their rules; see the teams output."
  }
}

output "fallbacks" {
  value = { for k, m in local.models : "${m.team}-${m.alias}" => "${m.team}-${m.fallback}" if m.fallback != null }
}
