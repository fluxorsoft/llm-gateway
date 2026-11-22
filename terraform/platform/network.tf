# A network of its own, with no NAT: nothing in it can reach the internet. Google APIs are reached privately.
resource "google_compute_network" "this" {
  name                    = var.name
  auto_create_subnetworks = false
  routing_mode            = "GLOBAL"
}

resource "google_compute_subnetwork" "this" {
  for_each                 = var.regions
  name                     = "${var.name}-${each.key}"
  region                   = each.key
  network                  = google_compute_network.this.id
  ip_cidr_range            = each.value.subnet
  private_ip_google_access = true
}

resource "google_compute_subnetwork" "proxy" {
  for_each      = var.regions
  name          = "${var.name}-proxy-${each.key}"
  region        = each.key
  network       = google_compute_network.this.id
  ip_cidr_range = each.value.proxy
  purpose       = "GLOBAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

# ---- the front door: one name, an address in each region, one set of rules --------------------------------------------
resource "google_compute_address" "front" {
  for_each     = var.regions
  name         = "${var.name}-${each.key}"
  region       = each.key
  subnetwork   = google_compute_subnetwork.this[each.key].id
  address_type = "INTERNAL"
}

resource "google_compute_region_network_endpoint_group" "gateway" {
  for_each              = var.regions
  name                  = "${var.name}-${each.key}"
  region                = each.key
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.gateway[each.key].name
  }
}

resource "google_compute_backend_service" "gateway" {
  name                  = var.name
  load_balancing_scheme = "INTERNAL_MANAGED"
  protocol              = "HTTPS"
  dynamic "backend" {
    for_each = google_compute_region_network_endpoint_group.gateway
    content {
      group = backend.value.id
    }
  }
}

# Only the inference paths pass; anything else gets a 404 from the load balancer. The gateway's filter checks again.
resource "google_compute_url_map" "this" {
  name = var.name
  default_route_action {
    weighted_backend_services {
      backend_service = google_compute_backend_service.gateway.id
      weight          = 100
    }
    fault_injection_policy {
      abort {
        http_status = 404
        percentage  = 100
      }
    }
  }
  host_rule {
    hosts        = [var.domain]
    path_matcher = "inference"
  }
  path_matcher {
    name = "inference"
    default_route_action {
      weighted_backend_services {
        backend_service = google_compute_backend_service.gateway.id
        weight          = 100
      }
      fault_injection_policy {
        abort {
          http_status = 404
          percentage  = 100
        }
      }
    }
    path_rule {
      paths   = ["/v1/chat/completions", "/v1/embeddings", "/v1/models"]
      service = google_compute_backend_service.gateway.id
    }
  }
}

resource "google_compute_ssl_policy" "this" {
  name            = var.name
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"
}

resource "google_compute_target_https_proxy" "this" {
  name                             = var.name
  url_map                          = google_compute_url_map.this.id
  ssl_policy                       = google_compute_ssl_policy.this.id
  certificate_manager_certificates = local.certificate_ids
}

resource "google_compute_global_forwarding_rule" "front" {
  for_each              = var.regions
  name                  = "${var.name}-${each.key}"
  load_balancing_scheme = "INTERNAL_MANAGED"
  network               = google_compute_network.this.id
  subnetwork            = google_compute_subnetwork.this[each.key].id
  ip_address            = google_compute_address.front[each.key].id
  port_range            = "443"
  target                = google_compute_target_https_proxy.this.id
  depends_on            = [google_compute_subnetwork.proxy]
}

resource "google_dns_managed_zone" "this" {
  name       = var.name
  dns_name   = "${var.domain}."
  visibility = "private"
  private_visibility_config {
    networks {
      network_url = google_compute_network.this.id
    }
  }
}

resource "google_dns_record_set" "front" {
  managed_zone = google_dns_managed_zone.this.name
  name         = "${var.domain}."
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

# ---- certificate: yours, or a private CA for a trial -------------------------------------------------------------------
locals {
  private_ca      = length(var.certificate_ids) == 0
  certificate_ids = local.private_ca ? [google_certificate_manager_certificate.trial[0].id] : var.certificate_ids
  ca_pem          = local.private_ca ? tls_self_signed_cert.ca[0].cert_pem : ""
}

resource "tls_private_key" "ca" {
  count     = local.private_ca ? 1 : 0
  algorithm = "RSA"
}

resource "tls_self_signed_cert" "ca" {
  count                 = local.private_ca ? 1 : 0
  private_key_pem       = tls_private_key.ca[0].private_key_pem
  is_ca_certificate     = true
  validity_period_hours = 720
  allowed_uses          = ["cert_signing", "crl_signing"]
  set_subject_key_id    = true
  subject {
    common_name = "${var.name} trial CA"
  }
}

resource "tls_private_key" "front" {
  count     = local.private_ca ? 1 : 0
  algorithm = "RSA"
}

resource "tls_cert_request" "front" {
  count           = local.private_ca ? 1 : 0
  private_key_pem = tls_private_key.front[0].private_key_pem
  dns_names       = [var.domain]
  subject {
    common_name = var.domain
  }
}

resource "tls_locally_signed_cert" "front" {
  count                 = local.private_ca ? 1 : 0
  cert_request_pem      = tls_cert_request.front[0].cert_request_pem
  ca_private_key_pem    = tls_private_key.ca[0].private_key_pem
  ca_cert_pem           = tls_self_signed_cert.ca[0].cert_pem
  validity_period_hours = 720
  allowed_uses          = ["digital_signature", "key_encipherment", "server_auth"]
}

resource "google_certificate_manager_certificate" "trial" {
  count = local.private_ca ? 1 : 0
  name  = "${var.name}-trial"
  scope = "ALL_REGIONS"
  self_managed {
    pem_certificate = tls_locally_signed_cert.front[0].cert_pem
    pem_private_key = tls_private_key.front[0].private_key_pem
  }
}
