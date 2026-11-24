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
    [for p in distinct([for t, v in local.whole : v.project]) : "project '${p}' is used by more than one team" if length([for t, v in local.whole : t if v.project == p]) > 1],
  )
}

output "teams" {
  value = { for t, v in local.whole : t => merge(v, { cloud = try(v.cloud, "gcp") }) }
  precondition {
    condition     = length(local.errors) == 0
    error_message = "The settings break these rules:\n- ${join("\n- ", local.errors)}"
  }
}

output "errors" { value = local.errors }

output "schema" { value = local.schema }

output "key_maker" { value = local.key_maker }

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
