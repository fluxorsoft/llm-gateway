# The two example apps, one in each team's project. Same gateway address; a different project, identity, key and model.
terraform {
  required_version = ">= 1.11"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.46" }
  }
  backend "gcs" {}
}

variable "gateway_project" { type = string }
variable "name" {
  type    = string
  default = "llmgw"
}
variable "image" { type = string }
variable "address" { type = string }
variable "ca_pem" {
  type    = string
  default = ""
}
variable "repository" {
  type        = object({ location = string, name = string })
  description = "The Artifact Registry repository the apps' image is in"
}
variable "default_urls" {
  type    = string
  default = ""
}
variable "members" {
  type        = list(string)
  default     = []
  description = "Who may open the apps"
}
variable "apps" {
  type = map(object({ project = string, region = string, app = string, secret = string, network = string, subnet = string, model = string, other_model = string, provider_tools = optional(list(string), []) }))
}

data "google_project" "team" {
  for_each   = var.apps
  project_id = each.value.project
}

# each team project's Cloud Run may pull the image
resource "google_artifact_registry_repository_iam_member" "pull" {
  for_each   = var.apps
  project    = var.gateway_project
  location   = var.repository.location
  repository = var.repository.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:service-${data.google_project.team[each.key].number}@serverless-robot-prod.iam.gserviceaccount.com"
}

locals {
  environment = { for t, a in var.apps : t => merge({
    APP          = t
    LLM_TEAM     = t
    LLM_URL      = var.address
    LLM_MODEL    = a.model
    LLM_CA_PEM   = var.ca_pem
    OTHER_MODEL  = a.other_model
    TEAM_PROJECT = a.project
    DEFAULT_URLS = var.default_urls
  }, length(a.provider_tools) == 0 ? {} : { PROVIDER_TOOLS = join(",", a.provider_tools) }) } # only for a team that has them
}

resource "google_cloud_run_v2_service" "app" {
  for_each            = var.apps
  project             = each.value.project
  name                = "${each.key}-app"
  location            = each.value.region
  deletion_protection = false
  template {
    service_account = each.value.app
    timeout         = "300s"
    scaling {
      max_instance_count = 1
    }
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = each.value.network
        subnetwork = each.value.subnet
      }
    }
    containers {
      image = var.image
      dynamic "env" {
        for_each = local.environment[each.key]
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name = "LLM_KEY"
        value_source {
          secret_key_ref {
            secret  = each.value.secret
            version = "latest" # read when an instance starts: redeploy to take a new key
          }
        }
      }
    }
  }
  depends_on = [google_artifact_registry_repository_iam_member.pull]
}

resource "google_cloud_run_v2_service_iam_member" "app" {
  for_each = merge([for t, a in var.apps : { for m in var.members : "${t}/${m}" => { team = t, member = m } }]...)
  project  = var.apps[each.value.team].project
  location = var.apps[each.value.team].region
  name     = google_cloud_run_v2_service.app[each.value.team].name
  role     = "roles/run.invoker"
  member   = each.value.member
}

# the checks, run as the team's app
resource "google_cloud_run_v2_job" "check" {
  for_each            = var.apps
  project             = each.value.project
  name                = "${each.key}-check"
  location            = each.value.region
  deletion_protection = false
  template {
    template {
      service_account = each.value.app
      max_retries     = 0
      timeout         = "1200s"
      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = each.value.network
          subnetwork = each.value.subnet
        }
      }
      containers {
        image   = var.image
        command = ["python", "check.py"]
        dynamic "env" {
          for_each = local.environment[each.key]
          content {
            name  = env.key
            value = env.value
          }
        }
        env {
          name = "LLM_KEY"
          value_source {
            secret_key_ref {
              secret  = each.value.secret
              version = "latest"
            }
          }
        }
      }
    }
  }
  depends_on = [google_artifact_registry_repository_iam_member.pull]
}

output "urls" { value = { for t, s in google_cloud_run_v2_service.app : t => s.uri } }
