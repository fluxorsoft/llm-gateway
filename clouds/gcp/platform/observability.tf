# Usage for chargeback, and the alerts worth waking someone for.
resource "google_bigquery_dataset" "usage" {
  count                      = var.usage_bigquery ? 1 : 0
  dataset_id                 = "${replace(var.name, "-", "_")}_usage"
  location                   = var.primary_region
  delete_contents_on_destroy = !var.deletion_protection
}

resource "google_logging_project_sink" "usage" {
  count                  = var.usage_bigquery ? 1 : 0
  name                   = "${var.name}-usage"
  destination            = "bigquery.googleapis.com/${google_bigquery_dataset.usage[0].id}"
  filter                 = local.door_on ? "resource.type=\"cloud_run_revision\" AND (resource.labels.service_name=\"${var.name}-gateway\" OR resource.labels.service_name=\"${var.name}-developer\") AND jsonPayload.message=\"llm usage\"" : "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.name}-gateway\" AND jsonPayload.message=\"llm usage\""
  unique_writer_identity = true
  bigquery_options {
    use_partitioned_tables = true
  }
}

resource "google_bigquery_dataset_iam_member" "usage" {
  count      = var.usage_bigquery ? 1 : 0
  dataset_id = google_bigquery_dataset.usage[0].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.usage[0].writer_identity
}

locals {
  from_gateway = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.name}-gateway\""
  counters = {
    # a location failed, whether or not another one then answered. A caller's own limits and refusals do not count
    model_failures = "${local.from_gateway} AND jsonPayload.message=\"llm usage\" AND jsonPayload.llm_usage.error=~\"^(Timeout|InternalServerError|ServiceUnavailableError|BadGatewayError|APIConnectionError|APIError|NotFoundError|PermissionDeniedError|RateLimitError|RouterRateLimitError)$\""
    # without Redis the limits are counted per instance, and nothing else shows it
    redis_failures = "${local.from_gateway} AND jsonPayload.message=~\"(?i)redis\" AND jsonPayload.level=\"ERROR\""
    armor_failures = "${local.from_gateway} AND jsonPayload.message=~\"Model Armor API error\""
    refused        = "${local.from_gateway} AND jsonPayload.message=\"gateway refused\" AND jsonPayload.refused.code=400"
    config_failed  = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${var.name}-config\" AND jsonPayload.message=\"config run failed\""
  }
  alerts = {
    model_failures = { title = "models are failing in a location", above = 1 }
    redis_failures = { title = "Redis is failing: limits are per instance", above = 3 }
    armor_failures = { title = "Model Armor is failing: calls are being refused", above = 3 }
    refused        = { title = "requests with fields that are not allowed", above = 20 }
    config_failed  = { title = "the LiteLLM settings run failed, or found changes made by hand", above = 0 }
  }
  channels = var.alert_email == "" ? [] : [google_monitoring_notification_channel.email[0].id]
}

resource "google_logging_metric" "this" {
  for_each = local.counters
  name     = "${var.name}/${each.key}"
  filter   = each.value
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  display_name = "${var.name} alerts"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_monitoring_alert_policy" "counter" {
  for_each              = local.alerts
  display_name          = "${var.name}: ${each.value.title}"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = each.value.title
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.this[each.key].name}\" AND resource.type=\"${each.key == "config_failed" ? "cloud_run_job" : "cloud_run_revision"}\""
      comparison      = "COMPARISON_GT"
      threshold_value = each.value.above
      duration        = "0s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "server_errors" {
  display_name          = "${var.name}: the gateway is answering 5xx"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = "5xx answers"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.name}-gateway\" AND metric.labels.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "0s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "database" {
  display_name          = "${var.name}: the database is down"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = "database not up"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/up\" AND resource.type=\"cloudsql_database\" AND resource.labels.database_id=\"${var.project}:${google_sql_database_instance.this.name}\""
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "120s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MIN"
      }
    }
  }
}

output "address" { value = "https://${var.domain}" }
output "front_addresses" { value = { for r, a in google_compute_address.front : r => a.address } }
output "admin_url" { value = google_cloud_run_v2_service.admin.uri }
output "ca_pem" { value = local.ca_pem }
output "gateway_default_urls" {
  value = join(",", [for r in keys(var.regions) : "https://${google_cloud_run_v2_service.gateway[r].name}-${data.google_project.this.number}.${r}.run.app"])
}
output "sql_instance" { value = google_sql_database_instance.this.name }
output "images" { value = var.images }
output "runner_job" { value = google_cloud_run_v2_job.runner.name }
output "teams" {
  value = { for t, m in module.team : t => { project = module.settings.teams[t].project, app = m.app_email, secret = m.secret, network = m.network, subnet = m.subnet, region = try(module.settings.teams[t].network.region, var.primary_region) } }
}

data "google_project" "this" {}

# ---- budget alerts: to the team's owner, and to the platform team -------------------------------------------------------
locals {
  owners = var.budget_alerts ? toset([for t in module.settings.teams : t.owner if can(regex("@", t.owner))]) : toset([])
  budget_levels = var.budget_alerts ? merge([for t, v in module.settings.teams : {
    "${t}/80"  = { team = t, owner = v.owner, level = "80", words = "has spent 80 per cent of its monthly budget" }
    "${t}/100" = { team = t, owner = v.owner, level = "100", words = "has spent its monthly budget: calls are refused" }
  }]...) : {}
}

resource "google_monitoring_notification_channel" "owner" {
  for_each     = local.owners
  display_name = "${var.name} budget: ${each.key}"
  type         = "email"
  labels       = { email_address = each.key }
}

resource "google_monitoring_alert_policy" "budget" {
  for_each              = local.budget_levels
  display_name          = "${var.name}: team ${each.value.team} ${each.value.words}"
  combiner              = "OR"
  notification_channels = concat(local.channels, contains(local.owners, each.value.owner) ? [google_monitoring_notification_channel.owner[each.value.owner].id] : [])
  conditions {
    display_name = "${each.value.team} at ${each.value.level} per cent"
    condition_matched_log {
      filter = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${var.name}-config\" AND jsonPayload.message=\"llm budget\" AND jsonPayload.team=\"${each.value.team}\" AND jsonPayload.level=\"${each.value.level}\""
    }
  }
  alert_strategy {
    notification_rate_limit {
      period = "86400s" # once a day while it lasts
    }
    auto_close = "604800s"
  }
}

resource "google_monitoring_alert_policy" "price_check" {
  count                 = var.price_check != null && var.usage_bigquery ? 1 : 0
  display_name          = "${var.name}: the gateway's costs differ from the bill"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = "price check drift"
    condition_matched_log {
      filter = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${var.name}-config\" AND jsonPayload.message=\"llm price check\" AND jsonPayload.level=\"drift\""
    }
  }
  alert_strategy {
    notification_rate_limit {
      period = "86400s"
    }
    auto_close = "604800s"
  }
}

# ---- keeping the words (capture): to the team's own project, never kept here ----------------------------------------------
resource "google_logging_project_exclusion" "words" {
  count  = length(module.settings.capture) == 0 ? 0 : 1
  name   = "${var.name}-words-not-here"
  filter = "jsonPayload.message=\"llm words\""
}

resource "google_logging_project_sink" "words" {
  for_each               = module.settings.capture
  name                   = "${var.name}-words-${each.key}"
  destination            = "bigquery.googleapis.com/projects/${module.settings.teams[each.key].project}/datasets/${module.team[each.key].words_dataset}"
  filter                 = "resource.type=\"cloud_run_revision\" AND jsonPayload.message=\"llm words\" AND jsonPayload.team=\"${each.key}\""
  unique_writer_identity = true
  bigquery_options {
    use_partitioned_tables = true
  }
}

resource "google_bigquery_dataset_iam_member" "words_writer" {
  for_each   = module.settings.capture
  project    = module.settings.teams[each.key].project
  dataset_id = module.team[each.key].words_dataset
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.words[each.key].writer_identity
}
