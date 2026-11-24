# The developers' door (developer_door): the same image and settings as the gateway, run as GATEWAY_ROLE=developer, behind
# the same load balancer under its own name. It speaks what coding tools send (the Responses API, the Gemini API), checks
# questions whole and does not hold answers back. Off unless developer_door is set: nothing here exists then.
locals {
  door_on      = var.developer_door != null
  door_regions = local.door_on ? keys(var.regions) : []
  door_domain  = local.door_on ? coalesce(try(var.developer_door.domain, null), "dev.${var.domain}") : ""
}

resource "google_cloud_run_v2_service" "developer" {
  for_each             = toset(local.door_regions)
  name                 = "${var.name}-developer"
  location             = each.key
  ingress              = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  invoker_iam_disabled = true # LiteLLM keys authenticate callers; the network and the load balancer limit who can ask
  default_uri_disabled = true # only the load balancer reaches it
  deletion_protection  = var.deletion_protection
  scaling {
    min_instance_count = try(var.developer_door.always_on, "primary") == "all" || (try(var.developer_door.always_on, "primary") == "primary" && each.key == var.primary_region) ? 1 : 0
  }
  template {
    service_account                  = google_service_account.gateway.email
    timeout                          = "3600s" # a coding task can stream for a long time
    max_instance_request_concurrency = 80
    scaling {
      max_instance_count = try(var.developer_door.maximum, 1)
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
        for_each = local.environment["developer-${each.key}"]
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.secret_environment["developer-${each.key}"]
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

resource "google_compute_region_network_endpoint_group" "developer" {
  for_each              = toset(local.door_regions)
  name                  = "${var.name}-developer-${each.key}"
  region                = each.key
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.developer[each.key].name
  }
}

resource "google_compute_backend_service" "developer" {
  count                 = local.door_on ? 1 : 0
  name                  = "${var.name}-developer"
  load_balancing_scheme = "INTERNAL_MANAGED"
  protocol              = "HTTPS"
  # no timeout_sec: Google refuses one for Cloud Run backends; the service's own timeout (3600 s) applies
  dynamic "backend" {
    for_each = google_compute_region_network_endpoint_group.developer
    content {
      group = backend.value.id
    }
  }
}

resource "google_dns_managed_zone" "developer" {
  count      = local.door_on ? 1 : 0
  name       = "${var.name}-developer"
  dns_name   = "${local.door_domain}."
  visibility = "private"
  private_visibility_config {
    networks {
      network_url = google_compute_network.this.id
    }
  }
}

resource "google_dns_record_set" "developer" {
  count        = local.door_on ? 1 : 0
  managed_zone = google_dns_managed_zone.developer[0].name
  name         = "${local.door_domain}."
  type         = "A"
  ttl          = 30
  routing_policy {
    dynamic "geo" {
      for_each = var.regions
      content {
        location = geo.key
        health_checked_targets {
          internal_load_balancers {
            load_balancer_type = "globalL7ilb"
            ip_address         = google_compute_address.front[geo.key].address
            port               = "443"
            ip_protocol        = "tcp"
            network_url        = google_compute_network.this.id
            project            = var.project
          }
        }
      }
    }
  }
  depends_on = [google_compute_global_forwarding_rule.front]
}

output "developer_address" {
  value = local.door_on ? "https://${local.door_domain}" : null
}
