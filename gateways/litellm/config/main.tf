# Step 2: what LiteLLM serves. Models, teams, failover and keys, from the settings files, through the admin service.
# Runs in the runner (a Cloud Run job inside the VPC); see gateways/litellm/runner/.
terraform {
  required_version = ">= 1.11"
  required_providers {
    litellm = { source = "BerriAI/litellm", version = "~> 1.108.0" } # the version of the proxy
    google  = { source = "hashicorp/google", version = "~> 7.46" }
    random  = { source = "hashicorp/random", version = "~> 3.7" }
  }
  backend "gcs" {}
}

variable "settings_dir" {
  type    = string
  default = "../../../settings/example"
}

variable "admin_url" {
  type    = string
  default = "http://127.0.0.1:9000" # onedoorai admin (src/onedoorai/admin.py)
}

variable "master_key" {
  type      = string
  sensitive = true
  ephemeral = true
}

provider "litellm" {
  api_base = var.admin_url
  api_key  = var.master_key
}

module "settings" {
  source = "../../../core/settings"
  dir    = var.settings_dir
}

locals {
  teams = module.settings.teams
  names = { for t in keys(local.teams) : t => distinct([for d in module.settings.deployments : d.name if d.team == t]) }
}

resource "litellm_model" "this" {
  for_each            = module.settings.deployments
  model_name          = each.value.name
  custom_llm_provider = "vertex_ai"
  base_model          = each.value.model
  vertex_project      = each.value.project
  vertex_location     = each.value.location
  mode                = "chat"
  additional_litellm_params = {
    order          = tostring(each.value.order)
    timeout        = tostring(each.value.timeout)
    stream_timeout = tostring(each.value.timeout)
  }
}

resource "litellm_fallback" "this" {
  for_each        = module.settings.fallbacks
  model           = each.key
  fallback_models = [each.value]
  depends_on      = [litellm_model.this]
}

resource "litellm_team" "this" {
  for_each        = local.teams
  team_alias      = each.key
  models          = local.names[each.key]
  max_budget      = each.value.budget_monthly_usd
  budget_duration = "monthly"
  rpm_limit       = each.value.limits.rpm
  tpm_limit       = each.value.limits.tpm
  metadata        = { owner = each.value.owner }
  depends_on      = [litellm_model.this]
}

# Keys are made by the key step after this one (onedoorai keys, run by the runner), so a key's value is never in Terraform
# state. The keys and secret versions that 1.0.0 made here stay where they are: Terraform forgets them, the key step adopts
# them by name, and nothing is revoked.
removed {
  from = litellm_key.this
  lifecycle {
    destroy = false
  }
}

removed {
  from = google_secret_manager_secret_version.key
  lifecycle {
    destroy = false
  }
}

# what the key step makes, keeps or deletes
output "keys" {
  value = { for t, v in local.teams : t => {
    keys    = v.keys
    days    = v.key_days
    project = v.project
    models  = local.names[t]
    maker   = module.settings.key_maker
  } }
}

output "names" { value = local.names }
