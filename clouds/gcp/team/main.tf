# One team's project.
terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.46" }
  }
}

variable "team" { type = string }
variable "project" { type = string }
variable "gateway_email" { type = string }
variable "runner_email" { type = string }
variable "app_service_account" { type = string }
variable "gateway_project" { type = string }
variable "gateway_network_id" { type = string }
variable "domain" { type = string }
variable "name" { type = string }
variable "members" {
  type        = list(object({ id = string, handle = string, daily_usd = optional(number) }))
  default     = []
  description = "People with a personal key; each gets a secret only they can read"
}

variable "peer_after" {
  type        = list(string)
  default     = []
  description = "The network peering waits for these, and nothing else does: a teardown must not wait for a subnet"
}
variable "network" {
  type        = object({ region = string, cidr = string })
  default     = null
  description = "A network for the team's app, peered to the gateway. Null when the project already reaches it (Shared VPC)"
}

resource "google_project_service" "this" {
  for_each           = toset(["aiplatform", "secretmanager", "iam", "run", "compute", "dns"])
  project            = var.project
  service            = "${each.key}.googleapis.com"
  disable_on_destroy = false
}

# ---- the gateway: predict, and nothing else --------------------------------------------------------------------------
resource "google_project_iam_custom_role" "predict" {
  project     = var.project
  role_id     = "llmGatewayPredict"
  title       = "LLM gateway: predict only"
  permissions = ["aiplatform.endpoints.predict"]
  depends_on  = [google_project_service.this]
}

resource "google_project_iam_member" "gateway" {
  project = var.project
  role    = google_project_iam_custom_role.predict.id
  member  = "serviceAccount:${var.gateway_email}"
}

# ---- the team's key: the runner writes it, the app reads it ------------------------------------------------------------
resource "google_service_account" "app" {
  project      = var.project
  account_id   = var.app_service_account
  display_name = "${var.team}: calls the LLM gateway (no model access of its own)"
  depends_on   = [google_project_service.this]
}

resource "google_secret_manager_secret" "key" {
  project   = var.project
  secret_id = "llm-gateway-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.this]
}

resource "google_secret_manager_secret_iam_member" "app" {
  project   = var.project
  secret_id = google_secret_manager_secret.key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}

resource "google_secret_manager_secret_iam_member" "runner" {
  for_each  = toset(["roles/secretmanager.secretVersionManager", "roles/secretmanager.secretAccessor"])
  project   = var.project
  secret_id = google_secret_manager_secret.key.id
  role      = each.key
  member    = "serviceAccount:${var.runner_email}"
}

# ---- the way to the gateway, when the project has none -----------------------------------------------------------------
resource "google_compute_network" "app" {
  count                   = var.network == null ? 0 : 1
  project                 = var.project
  name                    = "${var.name}-app"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.this]
}

resource "google_compute_subnetwork" "app" {
  count                    = var.network == null ? 0 : 1
  project                  = var.project
  name                     = "${var.name}-app"
  region                   = var.network.region
  network                  = google_compute_network.app[0].id
  ip_cidr_range            = var.network.cidr
  private_ip_google_access = true
}

resource "terraform_data" "peer_after" {
  input = var.peer_after
}

resource "google_compute_network_peering" "to_gateway" {
  count        = var.network == null ? 0 : 1
  name         = "${var.name}-gateway"
  network      = google_compute_network.app[0].id
  peer_network = var.gateway_network_id
  depends_on   = [terraform_data.peer_after]
}

resource "google_compute_network_peering" "from_gateway" {
  count        = var.network == null ? 0 : 1
  name         = "${var.name}-${var.team}"
  network      = var.gateway_network_id
  peer_network = google_compute_network.app[0].id
  depends_on   = [google_compute_network_peering.to_gateway]
}

resource "google_dns_managed_zone" "gateway" {
  count      = var.network == null ? 0 : 1
  project    = var.project
  name       = "${var.name}-gateway"
  dns_name   = "${var.domain}."
  visibility = "private"
  private_visibility_config {
    networks {
      network_url = google_compute_network.app[0].id
    }
  }
  peering_config {
    target_network {
      network_url = var.gateway_network_id
    }
  }
}

# ---- personal keys: the runner writes them, only the person reads theirs -------------------------------------------------
resource "google_secret_manager_secret" "person" {
  for_each  = { for m in var.members : m.handle => m }
  project   = var.project
  secret_id = "llm-key-${each.key}"
  replication {
    auto {}
  }
  depends_on = [google_project_service.this]
}

resource "google_secret_manager_secret_iam_member" "person" {
  for_each  = google_secret_manager_secret.person
  project   = var.project
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "user:${[for m in var.members : m.id if m.handle == each.key][0]}"
}

resource "google_secret_manager_secret_iam_member" "person_runner" {
  for_each  = { for p in setproduct(keys(google_secret_manager_secret.person), ["roles/secretmanager.secretVersionManager", "roles/secretmanager.secretAccessor"]) : "${p[0]}/${p[1]}" => p }
  project   = var.project
  secret_id = google_secret_manager_secret.person[each.value[0]].id
  role      = each.value[1]
  member    = "serviceAccount:${var.runner_email}"
}

output "app_email" { value = google_service_account.app.email }
output "secret" { value = google_secret_manager_secret.key.secret_id }
output "subnet" { value = one(google_compute_subnetwork.app[*].id) }
output "network" { value = one(google_compute_network.app[*].id) }
