# What each team's project gets: the gateway may predict there, and only the team's app may read the team's key.
module "settings" {
  source = "../../../core/settings"
  dir    = var.settings_dir
}

module "team" {
  source   = "../team"
  for_each = module.settings.teams

  team                = each.key
  project             = each.value.project
  gateway_email       = google_service_account.gateway.email
  runner_email        = google_service_account.runner.email
  app_service_account = each.value.app_service_account
  network             = try(each.value.network, null)
  members             = each.value.members
  capture             = try(module.settings.capture[each.key], null)
  gateway_project     = var.project
  gateway_network_id  = google_compute_network.this.id
  domain              = var.domain
  name                = var.name
  peer_after          = [for r in google_redis_instance.this : r.id] # Redis peers the same network, one at a time
}
