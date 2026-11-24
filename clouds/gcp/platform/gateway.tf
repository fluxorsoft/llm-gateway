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
  )
  # the platform's switches for LiteLLM's own config, laid over it when the gateway starts (onedoorai.litellm.guard)
  gateway_settings = merge(
    var.spend_log_days == null ? {} : { general_settings = { maximum_spend_logs_retention_period = "${var.spend_log_days}d" } },
  )
  environment = {
    for k, s in local.services : k => merge(length(local.gateway_settings) == 0 ? {} : { GATEWAY_SETTINGS = jsonencode(local.gateway_settings) }, {
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
