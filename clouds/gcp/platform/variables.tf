# Step 1: the platform. Everything here is Google Cloud; LiteLLM's own settings are in gateways/litellm/config.
terraform {
  required_version = ">= 1.11"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.46" }
    random = { source = "hashicorp/random", version = "~> 3.7" }
    tls    = { source = "hashicorp/tls", version = "~> 4.1" }
  }
  backend "gcs" {}
}

provider "google" {
  project = var.project
}

variable "project" {
  type        = string
  description = "The gateway project"
}

variable "name" {
  type    = string
  default = "llmgw"
}

variable "settings_dir" {
  type    = string
  default = "../../../settings/example"
}

variable "primary_region" {
  type        = string
  default     = "europe-west2"
  description = "Holds the database and the admin service"
}

variable "regions" {
  type        = map(object({ subnet = string, proxy = string }))
  description = "Gateway regions: one, or two for failover"
  default = {
    europe-west2 = { subnet = "10.60.0.0/24", proxy = "10.60.8.0/23" }
    europe-west1 = { subnet = "10.60.1.0/24", proxy = "10.60.10.0/23" }
  }
  validation {
    condition     = contains(keys(var.regions), var.primary_region)
    error_message = "primary_region must be one of the regions."
  }
}

variable "domain" {
  type        = string
  default     = "llm.internal.example.com"
  description = "The one private name every app uses"
}

variable "certificate_ids" {
  type        = list(string)
  default     = []
  description = "Certificate Manager certificates (scope ALL_REGIONS) for the domain. Empty makes a private CA: for trials"
}

variable "developer_door" {
  type = object({
    domain    = optional(string)            # default: dev.<domain>
    always_on = optional(string, "primary") # primary: one copy always running in the primary region; all; none (starts on demand)
    maximum   = optional(number, 1)         # instances per region; each opens up to 5 database connections
  })
  default     = null
  description = "A second door for developers' coding tools: streams answers as they come, speaks the Responses and Gemini APIs"
  validation {
    condition     = var.developer_door == null || contains(["primary", "all", "none"], try(var.developer_door.always_on, "primary"))
    error_message = "developer_door.always_on is primary, all or none."
  }
}

variable "budget_alerts" {
  type        = bool
  default     = false
  description = "Every hour, tell each team's owner (and alert_email) when the team has spent 80 and 100 per cent of its monthly budget"
}

variable "price_check" {
  type = object({
    billing_export = string               # the Cloud Billing export table, project.dataset.table (a billing admin turns the export on)
    above          = optional(number, 10) # alert when the two differ by more than this many per cent
  })
  default     = null
  description = "Every Monday, compare last week's gateway cost per team project with Google's bill for Vertex AI"
}

variable "db_replica" {
  type        = bool
  default     = false
  description = "A copy of the database in the second gateway region, kept up to date, for clouds/gcp/scripts/db_failover.sh"
}

variable "spend_log_days" {
  type        = number
  default     = null
  description = "Delete LiteLLM's spend rows older than this many days (BigQuery keeps the usage record). Null keeps them all"
  validation {
    condition     = var.spend_log_days == null || try(var.spend_log_days >= 1, false)
    error_message = "spend_log_days is 1 or more, or null."
  }
}

variable "canary" {
  type = object({
    region  = string # a gateway region other than the primary one
    gateway = string # the new gateway image, by digest
  })
  default     = null
  description = "The upgrade lane: the migration, the admin service and one region's gateway take the new image first; the other regions keep images.gateway until the checks pass"
  validation {
    condition     = var.canary == null || can(regex("@sha256:[0-9a-f]{64}$", var.canary.gateway))
    error_message = "canary.gateway must be given by digest (...@sha256:...)."
  }
}

variable "images" {
  type        = object({ gateway = string, runner = string, app = string })
  description = "Images by digest, as scripts/images.sh prints them"
  validation {
    condition     = alltrue([for i in values(var.images) : can(regex("@sha256:[0-9a-f]{64}$", i))])
    error_message = "Every image must be given by digest (...@sha256:...)."
  }
}

# ---- choices --------------------------------------------------------------------------------------------------------
variable "size" {
  type = object({
    db_tier         = optional(string, "db-g1-small")
    db_connections  = optional(number, 50)  # what the tier allows
    db_regional     = optional(bool, false) # zonal failover
    db_disk_gb      = optional(number, 10)
    redis_gb        = optional(number, 1)
    redis_ha        = optional(bool, false)
    gateway_memory  = optional(string, "4Gi")
    gateway_minimum = optional(number, 1)
    gateway_maximum = optional(number, 3)
  })
  default     = {}
  description = "The trial size; the README lists the production one"
  validation {
    condition     = var.size.gateway_minimum >= 1
    error_message = "gateway_minimum must be 1 or more: service health needs a running instance in each region."
  }
}

variable "no_uri_filter" {
  type        = list(string)
  default     = ["europe-west2"]
  description = "Regions where Model Armor does not offer the malicious URL filter"
}

variable "usage_bigquery" {
  type        = bool
  default     = true
  description = "Copy the usage records to BigQuery"
}

variable "drift_check" {
  type        = bool
  default     = false
  description = "A daily plan of step 2; it fails (and alerts) when LiteLLM was changed by hand"
}

variable "alert_email" {
  type        = string
  default     = ""
  description = "Empty: the alerts exist, and notify nobody"
}

variable "admin_members" {
  type        = list(string)
  default     = []
  description = "Who may reach the admin service (user:, group:); Cloud Run IAM checks it"
}

variable "admin_public_ingress" {
  type        = bool
  default     = false
  description = "Let admin_members reach the admin service from outside the VPC (IAM still applies)"
}

variable "deletion_protection" {
  type    = bool
  default = true
}
