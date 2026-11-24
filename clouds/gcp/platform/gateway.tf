# The gateways (one per region), the admin service, the migration job, and the runner that applies gateways/litellm/config.
resource "google_service_account" "gateway" {
  account_id   = "${var.name}-gateway"
  display_name = "LLM gateway: calls models, predict only"
}

resource "google_service_account" "admin" {
  account_id   = "${var.name}-admin"
  display_name = "LLM gateway admin service: no model access"
}

resource "google_service_account" "runner" {
  account_id   = "${var.name}-runner"
  display_name = "LLM gateway runner: applies LiteLLM's settings"
}

locals {
  core_secrets = { for k, s in google_secret_manager_secret.this : k => s.id }
  readers = merge(
    { for k, id in local.core_secrets : "gateway/${k}" => { secret = id, sa = google_service_account.gateway.email } },
    { for k, id in local.core_secrets : "admin/${k}" => { secret = id, sa = google_service_account.admin.email } },
    { "runner/master-key" = { secret = local.core_secrets["master-key"], sa = google_service_account.runner.email } },
    { for r in keys(var.regions) : "gateway/redis-${r}" => { secret = google_secret_manager_secret.redis[r].id, sa = google_service_account.gateway.email } },
    { for r in keys(var.regions) : "gateway/redis-ca-${r}" => { secret = google_secret_manager_secret.redis_ca[r].id, sa = google_service_account.gateway.email } },
    { "admin/redis" = { secret = google_secret_manager_secret.redis[var.primary_region].id, sa = google_service_account.admin.email } },
    { "admin/redis-ca" = { secret = google_secret_manager_secret.redis_ca[var.primary_region].id, sa = google_service_account.admin.email } },
  )
  # one definition for both roles: what differs is the region, the identity and the role
  services = merge(
    { for r in keys(var.regions) : "gateway-${r}" => { role = "gateway", region = r, sa = google_service_account.gateway.email } },
    { admin = { role = "admin", region = var.primary_region, sa = google_service_account.admin.email } },
    { for r in local.door_regions : "developer-${r}" => { role = "developer", region = r, sa = google_service_account.gateway.email } },
  )
  # the platform's switches for LiteLLM's own config, laid over it when the gateway starts (onedoorai.litellm.guard)
  gateway_settings = merge(
    var.spend_log_days == null ? {} : { general_settings = { maximum_spend_logs_retention_period = "${var.spend_log_days}d" } },
    length(module.settings.capture) == 0 ? {} : { litellm_settings = { callbacks = ["usage.usage", "capture.capture"] } },
  )
  environment = {
    for k, s in local.services : k => merge(length(local.gateway_settings) == 0 ? {} : { GATEWAY_SETTINGS = jsonencode(local.gateway_settings) }, s.role == "developer" ? { GATEWAY_DOOR = "developers" } : {}, module.settings.agents_on && s.role != "admin" ? { AGENTS_SECRET = "${var.project}/${var.name}-agents", GATEWAY_AUDIENCE = s.role == "developer" ? "https://${local.door_domain}" : "https://${var.domain}" } : {}, length(module.settings.capture) == 0 ? {} : { CAPTURE_TEAMS = jsonencode(keys(module.settings.capture)) }, length(module.settings.team_safety) == 0 ? {} : { ARMOR_TEMPLATES = jsonencode({ for t, n in module.settings.team_safety : t => "${var.name}-${n}" }) }, {
      GATEWAY_ROLE                                                                                        = s.role
      ARMOR_PROJECT                                                                                       = var.project
      ARMOR_TEMPLATE                                                                                      = google_model_armor_template.this[s.region].template_id
      ARMOR_LOCATION                                                                                      = s.region
      REDIS_HOST                                                                                          = google_redis_instance.this[s.region].host
      REDIS_PORT                                                                                          = tostring(google_redis_instance.this[s.region].port)
      REDIS_SSL                                                                                           = "True"
      REDIS_SSL_CA_CERTS                                                                                  = "/etc/redis/ca.pem"
    })
  }
  secret_environment = {
    for k, s in local.services : k => {
      DATABASE_URL       = google_secret_manager_secret_version.database_url.id
      LITELLM_MASTER_KEY = google_secret_manager_secret_version.master_key.id
      LITELLM_SALT_KEY   = google_secret_manager_secret_version.salt_key.id
      REDIS_PASSWORD     = google_secret_manager_secret_version.redis[s.region].id
    }
  }
}

resource "google_secret_manager_secret_iam_member" "reader" {
  for_each  = local.readers
  secret_id = each.value.secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value.sa}"
}

resource "google_project_iam_member" "armor" {
  project = var.project
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:${google_service_account.gateway.email}"
}

resource "google_model_armor_template" "this" {
  for_each    = var.regions
  template_id = var.name
  location    = each.key
  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }
    dynamic "malicious_uri_filter_settings" { # a region without it refuses even DISABLED
      for_each = contains(var.no_uri_filter, each.key) ? [] : [1]
      content {
        filter_enforcement = "ENABLED"
      }
    }
    rai_settings {
      dynamic "rai_filters" {
        for_each = ["DANGEROUS", "HARASSMENT", "HATE_SPEECH", "SEXUALLY_EXPLICIT"]
        content {
          filter_type      = rai_filters.value
          confidence_level = "MEDIUM_AND_ABOVE"
        }
      }
    }
    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }
  }
}

# The upgrade lane: with a canary, the migration and the admin service run the new image, and so does the canary region's
# gateway; every other region keeps images.gateway until the lane promotes it.
locals {
  core_image    = var.canary == null ? var.images.gateway : var.canary.gateway
  gateway_image = { for r in keys(var.regions) : r => var.canary != null && try(var.canary.region, "") == r ? var.canary.gateway : var.images.gateway }
}

check "canary_region" {
  assert {
    condition     = var.canary == null || (contains(keys(var.regions), try(var.canary.region, "")) && try(var.canary.region, "") != var.primary_region)
    error_message = "canary.region must be a gateway region other than the primary one."
  }
}

# The migration runs before any new revision: the apply waits for it.
resource "google_cloud_run_v2_job" "migrate" {
  name                = "${var.name}-migrate"
  location            = var.primary_region
  deletion_protection = false
  run_execution_token = substr(split("@sha256:", local.core_image)[1], 0, 12)
  template {
    template {
      service_account = google_service_account.admin.email
      max_retries     = 1
      timeout         = "600s"
      vpc_access {
        egress = "ALL_TRAFFIC"
        network_interfaces {
          network    = google_compute_network.this.id
          subnetwork = google_compute_subnetwork.this[var.primary_region].id
        }
      }
      containers {
        image   = local.core_image
        command = ["/app/.venv/bin/python", "/app/litellm/proxy/prisma_migration.py"]
        resources {
          limits = { cpu = "1", memory = "2Gi" }
        }
        env {
          name  = "DISABLE_SCHEMA_UPDATE"
          value = "false"
        }
        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this["database-url"].secret_id
              version = google_secret_manager_secret_version.database_url.version
            }
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.reader, google_dns_record_set.db, google_compute_forwarding_rule.db, google_sql_database.this]
}

resource "google_cloud_run_v2_service" "gateway" {
  for_each             = var.regions
  name                 = "${var.name}-gateway"
  location             = each.key
  ingress              = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  invoker_iam_disabled = true # LiteLLM keys authenticate callers; the network and the load balancer limit who can ask
  default_uri_disabled = true # only the load balancer reaches it
  deletion_protection  = var.deletion_protection
  scaling {
    min_instance_count = var.size.gateway_minimum
  }
  template {
    service_account                  = google_service_account.gateway.email
    timeout                          = "900s"
    max_instance_request_concurrency = 80
    scaling {
      max_instance_count = var.size.gateway_maximum
    }
    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = google_compute_network.this.id
        subnetwork = google_compute_subnetwork.this[each.key].id
      }
    }
    containers {
      image = local.gateway_image[each.key]
      ports {
        container_port = 8080
      }
      resources {
        limits            = { cpu = "1", memory = var.size.gateway_memory }
        cpu_idle          = false # spend is written between requests
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = local.environment["gateway-${each.key}"]
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.secret_environment["gateway-${each.key}"]
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = split("/", env.value)[3]
              version = split("/", env.value)[5]
            }
          }
        }
      }
      volume_mounts {
        name       = "redis-ca"
        mount_path = "/etc/redis"
      }
      startup_probe {
        initial_delay_seconds = 10
        period_seconds        = 5
        failure_threshold     = 40
        timeout_seconds       = 4
        http_get {
          path = "/health/liveliness"
        }
      }
      liveness_probe {
        period_seconds    = 30
        failure_threshold = 3
        timeout_seconds   = 10
        http_get {
          path = "/health/liveliness"
        }
      }
      readiness_probe { # the load balancer leaves a region whose instances fail this
        period_seconds    = 10
        failure_threshold = 2
        timeout_seconds   = 5
        http_get {
          path = "/health/readiness"
        }
      }
    }
    volumes {
      name = "redis-ca"
      secret {
        secret       = google_secret_manager_secret.redis_ca[each.key].secret_id
        default_mode = 292 # 0444
        items {
          path    = "ca.pem"
          version = google_secret_manager_secret_version.redis_ca[each.key].version
        }
      }
    }
  }
  depends_on = [google_cloud_run_v2_job.migrate, google_secret_manager_secret_iam_member.reader, google_dns_record_set.endpoint]
}

resource "google_cloud_run_v2_service" "admin" {
  name                = "${var.name}-admin"
  location            = var.primary_region
  ingress             = var.admin_public_ingress ? "INGRESS_TRAFFIC_ALL" : "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = var.deletion_protection
  template {
    service_account = google_service_account.admin.email
    timeout         = "300s"
    scaling {
      max_instance_count = 1 # scales to zero
    }
    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = google_compute_network.this.id
        subnetwork = google_compute_subnetwork.this[var.primary_region].id
      }
    }
    containers {
      image = local.core_image
      ports {
        container_port = 8080
      }
      resources {
        limits            = { cpu = "1", memory = var.size.gateway_memory }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = local.environment["admin"]
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.secret_environment["admin"]
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = split("/", env.value)[3]
              version = split("/", env.value)[5]
            }
          }
        }
      }
      volume_mounts {
        name       = "redis-ca"
        mount_path = "/etc/redis"
      }
      startup_probe {
        initial_delay_seconds = 10
        period_seconds        = 5
        failure_threshold     = 40
        timeout_seconds       = 4
        http_get {
          path = "/health/liveliness"
        }
      }
    }
    volumes {
      name = "redis-ca"
      secret {
        secret       = google_secret_manager_secret.redis_ca[var.primary_region].secret_id
        default_mode = 292
        items {
          path    = "ca.pem"
          version = google_secret_manager_secret_version.redis_ca[var.primary_region].version
        }
      }
    }
  }
  depends_on = [google_cloud_run_v2_job.migrate, google_secret_manager_secret_iam_member.reader]
}

resource "google_cloud_run_v2_service_iam_member" "admin" {
  for_each = merge({ for m in var.admin_members : m => m }, { runner = "serviceAccount:${google_service_account.runner.email}" })
  name     = google_cloud_run_v2_service.admin.name
  location = var.primary_region
  role     = "roles/run.invoker"
  member   = each.value
}

# ---- the runner --------------------------------------------------------------------------------------------------------
resource "google_storage_bucket" "config_state" {
  name                        = "${var.project}-${var.name}-config-state"
  location                    = var.primary_region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
  versioning {
    enabled = true
  }
}

resource "google_storage_bucket_iam_member" "runner" {
  bucket = google_storage_bucket.config_state.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_cloud_run_v2_job" "runner" {
  name                = "${var.name}-config"
  location            = var.primary_region
  deletion_protection = false
  template {
    template {
      service_account = google_service_account.runner.email
      max_retries     = 0
      timeout         = "900s"
      vpc_access {
        egress = "ALL_TRAFFIC"
        network_interfaces {
          network    = google_compute_network.this.id
          subnetwork = google_compute_subnetwork.this[var.primary_region].id
        }
      }
      containers {
        dynamic "env" {
          for_each = module.settings.agents_on ? { AGENTS_SECRET = "${var.project}/${var.name}-agents" } : {}
          content {
            name  = env.key
            value = env.value
          }
        }
        image = var.images.runner
        resources {
          limits = { cpu = "1", memory = "2Gi" }
        }
        env {
          name  = "MODE"
          value = "plan" # gcloud run jobs execute ... --update-env-vars=MODE=apply
        }
        env {
          name  = "ADMIN_URL"
          value = google_cloud_run_v2_service.admin.uri
        }
        env {
          name  = "STATE_BUCKET"
          value = google_storage_bucket.config_state.name
        }
        env {
          name = "TF_VAR_master_key"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this["master-key"].secret_id
              version = google_secret_manager_secret_version.master_key.version
            }
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.reader]
}

resource "google_service_account" "scheduler" {
  count      = var.drift_check ? 1 : 0
  account_id = "${var.name}-scheduler"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler" {
  count    = var.drift_check ? 1 : 0
  name     = google_cloud_run_v2_job.runner.name
  location = var.primary_region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler[0].email}"
}

resource "google_cloud_scheduler_job" "drift" {
  count    = var.drift_check ? 1 : 0
  name     = "${var.name}-drift"
  region   = var.primary_region
  schedule = "0 6 * * *"
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project}/locations/${var.primary_region}/jobs/${google_cloud_run_v2_job.runner.name}:run"
    oauth_token {
      service_account_email = google_service_account.scheduler[0].email
    }
  }
}

# the region the upgrade lane moves first: the first gateway region that is not the primary one
output "canary_region" {
  value = try([for r in keys(var.regions) : r if r != var.primary_region][0], null)
}

# ---- budget alerts: every hour the runner writes one line per team; an alert per team and level reads them --------------
resource "google_service_account" "budgets" {
  count      = var.budget_alerts ? 1 : 0
  account_id = "${var.name}-budgets"
}

resource "google_cloud_run_v2_job_iam_member" "budgets" {
  count    = var.budget_alerts ? 1 : 0
  name     = google_cloud_run_v2_job.runner.name
  location = var.primary_region
  role     = "roles/run.jobsExecutorWithOverrides" # MODE=budgets, and nothing else changes
  member   = "serviceAccount:${google_service_account.budgets[0].email}"
}

resource "google_cloud_scheduler_job" "budgets" {
  count    = var.budget_alerts ? 1 : 0
  name     = "${var.name}-budgets"
  region   = var.primary_region
  schedule = "15 * * * *"
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project}/locations/${var.primary_region}/jobs/${google_cloud_run_v2_job.runner.name}:run"
    body        = base64encode(jsonencode({ overrides = { containerOverrides = [{ env = [{ name = "MODE", value = "budgets" }] }] } }))
    headers     = { "Content-Type" = "application/json" }
    oauth_token {
      service_account_email = google_service_account.budgets[0].email
    }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.budgets]
}

# ---- the price check: every Monday, the gateway's cost figures against the bill -----------------------------------------
locals {
  billing_parts = var.price_check == null ? [] : split(".", var.price_check.billing_export)
}

check "billing_export" {
  assert {
    condition     = var.price_check == null || length(local.billing_parts) == 3
    error_message = "price_check.billing_export is project.dataset.table."
  }
}

resource "google_project_iam_member" "runner_bigquery" {
  count   = var.price_check != null && var.usage_bigquery ? 1 : 0
  project = var.project
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_usage" {
  count      = var.price_check != null && var.usage_bigquery ? 1 : 0
  dataset_id = google_bigquery_dataset.usage[0].dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_bigquery_dataset_iam_member" "runner_billing" {
  count      = var.price_check != null && var.usage_bigquery ? 1 : 0
  project    = local.billing_parts[0]
  dataset_id = local.billing_parts[1]
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_service_account" "pricecheck" {
  count      = var.price_check != null && var.usage_bigquery ? 1 : 0
  account_id = "${var.name}-pricecheck"
}

resource "google_cloud_run_v2_job_iam_member" "pricecheck" {
  count    = var.price_check != null && var.usage_bigquery ? 1 : 0
  name     = google_cloud_run_v2_job.runner.name
  location = var.primary_region
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.pricecheck[0].email}"
}

resource "google_cloud_scheduler_job" "pricecheck" {
  count    = var.price_check != null && var.usage_bigquery ? 1 : 0
  name     = "${var.name}-pricecheck"
  region   = var.primary_region
  schedule = "0 7 * * 1"
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project}/locations/${var.primary_region}/jobs/${google_cloud_run_v2_job.runner.name}:run"
    body = base64encode(jsonencode({ overrides = { containerOverrides = [{ env = [
      { name = "MODE", value = "pricecheck" },
      { name = "USAGE_DATASET", value = "${var.project}.${google_bigquery_dataset.usage[0].dataset_id}" },
      { name = "BILLING_EXPORT", value = var.price_check.billing_export },
      { name = "PRICE_CHECK_ABOVE", value = tostring(var.price_check.above) },
    ] }] } }))
    headers = { "Content-Type" = "application/json" }
    oauth_token {
      service_account_email = google_service_account.pricecheck[0].email
    }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.pricecheck]
}

# ---- safety rules per team: a Model Armor template per rule set and region -----------------------------------------------
resource "google_model_armor_template" "rules" {
  for_each    = { for p in setproduct(keys(module.settings.safety), keys(var.regions)) : "${p[0]}/${p[1]}" => { rules = module.settings.safety[p[0]], name = p[0], region = p[1] } }
  template_id = "${var.name}-${each.value.name}"
  location    = each.value.region
  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = each.value.rules.injection
    }
    dynamic "malicious_uri_filter_settings" {
      for_each = contains(var.no_uri_filter, each.value.region) ? [] : [1]
      content {
        filter_enforcement = "ENABLED"
      }
    }
    rai_settings {
      dynamic "rai_filters" {
        for_each = ["DANGEROUS", "HARASSMENT", "HATE_SPEECH", "SEXUALLY_EXPLICIT"]
        content {
          filter_type      = rai_filters.value
          confidence_level = each.value.rules.harmful
        }
      }
    }
    sdp_settings {
      basic_config {
        filter_enforcement = each.value.rules.sensitive_data ? "ENABLED" : "DISABLED"
      }
    }
  }
}

# ---- the admin record (audit_record): daily, kept unchanged for its retention period -----------------------------------
resource "google_storage_bucket" "audit" {
  count                       = var.audit_record == null ? 0 : 1
  name                        = "${var.project}-${var.name}-audit"
  location                    = var.primary_region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
  retention_policy {
    retention_period = var.audit_record.days * 86400
    is_locked        = var.audit_record.lock
  }
}

resource "google_storage_bucket_iam_member" "audit" {
  for_each = var.audit_record == null ? toset([]) : toset(["roles/storage.objectCreator", "roles/storage.objectViewer"])
  bucket   = google_storage_bucket.audit[0].name
  role     = each.key
  member   = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_service_account" "audit" {
  count      = var.audit_record == null ? 0 : 1
  account_id = "${var.name}-audit"
}

resource "google_cloud_run_v2_job_iam_member" "audit" {
  count    = var.audit_record == null ? 0 : 1
  name     = google_cloud_run_v2_job.runner.name
  location = var.primary_region
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.audit[0].email}"
}

resource "google_cloud_scheduler_job" "audit" {
  count    = var.audit_record == null ? 0 : 1
  name     = "${var.name}-audit"
  region   = var.primary_region
  schedule = "30 5 * * *"
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project}/locations/${var.primary_region}/jobs/${google_cloud_run_v2_job.runner.name}:run"
    body = base64encode(jsonencode({ overrides = { containerOverrides = [{ env = [
      { name = "MODE", value = "audit" }, { name = "AUDIT_BUCKET", value = google_storage_bucket.audit[0].name },
    ] }] } }))
    headers = { "Content-Type" = "application/json" }
    oauth_token {
      service_account_email = google_service_account.audit[0].email
    }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.audit]
}

# ---- agents: their keys, by identity, in a secret only the gateways read and only the runner writes ---------------------
resource "google_secret_manager_secret" "agents" {
  count     = module.settings.agents_on ? 1 : 0
  secret_id = "${var.name}-agents"
  replication {
    user_managed {
      dynamic "replicas" {
        for_each = var.regions
        content {
          location = replicas.key
        }
      }
    }
  }
}

resource "google_secret_manager_secret_iam_member" "agents" {
  for_each = module.settings.agents_on ? {
    gateway = { role = "roles/secretmanager.secretAccessor", member = google_service_account.gateway.email }
    reader  = { role = "roles/secretmanager.secretAccessor", member = google_service_account.runner.email }
    writer  = { role = "roles/secretmanager.secretVersionAdder", member = google_service_account.runner.email }
  } : {}
  secret_id = google_secret_manager_secret.agents[0].id
  role      = each.value.role
  member    = "serviceAccount:${each.value.member}"
}
