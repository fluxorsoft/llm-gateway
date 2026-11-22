# Private endpoints for the Google addresses Private Google Access does not cover (*.rep.googleapis.com): Model Armor in
# each region, and the eu location of the models. With these, and no NAT, the gateways reach Google and nothing else.
locals {
  # the eu location's front must be in an EU region that belongs to it; other regions reach it through global access
  eu_members = ["europe-west1", "europe-west3", "europe-west4", "europe-west8", "europe-west9", "europe-west10", "europe-central2", "europe-north1", "europe-southwest1"]
  eu_regions = [for r in keys(var.regions) : r if contains(local.eu_members, r)]
  uses       = distinct([for d in module.settings.deployments : d.location])
  endpoints = merge(
    { for r in keys(var.regions) : "armor-${r}" => { region = r, api = "modelarmor.${r}.rep.googleapis.com" } },
    contains(local.uses, "eu") ? { for r in local.eu_regions : "eu-${r}" => { region = r, api = "aiplatform.eu.rep.googleapis.com" } } : {},
  )
  hosts = distinct([for e in local.endpoints : e.api])
}

resource "google_network_connectivity_regional_endpoint" "this" {
  for_each          = local.endpoints
  name              = "${var.name}-${each.key}"
  location          = each.value.region
  target_google_api = each.value.api
  access_type       = startswith(each.key, "eu-") ? "GLOBAL" : "REGIONAL"
  network           = google_compute_network.this.id
  subnetwork        = google_compute_subnetwork.this[each.value.region].id
  lifecycle {
    precondition {
      condition     = !contains(local.uses, "eu") || length(local.eu_regions) > 0
      error_message = "A team uses the eu location, and no gateway region is in it: add one of ${join(", ", local.eu_members)} to regions."
    }
    precondition {
      condition     = !contains(local.uses, "us")
      error_message = "The us location needs an endpoint in a US region, which this module does not make."
    }
  }
}

resource "google_dns_managed_zone" "endpoint" {
  for_each   = toset(local.hosts)
  name       = "${var.name}-${replace(each.key, ".", "-")}"
  dns_name   = "${each.key}."
  visibility = "private"
  private_visibility_config {
    networks {
      network_url = google_compute_network.this.id
    }
  }
}

# the nearest endpoint, when there are several
resource "google_dns_record_set" "endpoint" {
  for_each     = toset(local.hosts)
  managed_zone = google_dns_managed_zone.endpoint[each.key].name
  name         = "${each.key}."
  type         = "A"
  ttl          = 60
  routing_policy {
    dynamic "geo" {
      for_each = { for k, e in local.endpoints : k => e if e.api == each.key }
      content {
        location = geo.value.region
        rrdatas  = [google_network_connectivity_regional_endpoint.this[geo.key].address]
      }
    }
  }
  depends_on = [google_network_connectivity_regional_endpoint.this]
}
