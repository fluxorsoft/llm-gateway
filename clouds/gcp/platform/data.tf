# The database, Redis and the secrets. Passwords and keys are made in memory and written with write-only arguments, so
# they are not in the state (Redis's password is: Google returns it as an attribute).
resource "random_id" "db" {
  byte_length = 3 # a deleted instance's name cannot be used again for a week
}

ephemeral "random_password" "db" {
  length  = 40
  special = false
}

ephemeral "random_password" "master" {
  length  = 48
  special = false
}

ephemeral "random_password" "salt" {
  length  = 48
  special = false
}

resource "google_sql_database_instance" "this" {
  name                = "${var.name}-${random_id.db.hex}"
  region              = var.primary_region
  database_version    = "POSTGRES_16"
  deletion_protection = var.deletion_protection
  settings {
    edition                     = "ENTERPRISE"
    tier                        = var.size.db_tier
    availability_type           = var.size.db_regional ? "REGIONAL" : "ZONAL"
    disk_size                   = var.size.db_disk_gb
    disk_autoresize             = true
    deletion_protection_enabled = var.deletion_protection
    ip_configuration {
      ipv4_enabled = false
      ssl_mode     = "ENCRYPTED_ONLY"
      psc_config {
        psc_enabled               = true
        allowed_consumer_projects = [var.project]
      }
    }
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "02:00"
    }
    insights_config {
      query_insights_enabled = true
    }
  }
  lifecycle {
    precondition {
      # 5 connections per instance (gateways/litellm/gateway/config.yaml): every gateway at its maximum, the admin service, one migration
      condition     = 5 * (length(var.regions) * var.size.gateway_maximum + length(local.door_regions) * try(var.developer_door.maximum, 1) + 1) + 5 <= var.size.db_connections
      error_message = "The gateways can open more connections than the database allows: lower gateway_maximum or raise db_tier and db_connections."
    }
  }
}

resource "google_sql_database" "this" {
  name            = "litellm"
  instance        = google_sql_database_instance.this.name
  deletion_policy = "ABANDON" # goes with the instance
}

resource "google_sql_user" "this" {
  name                = "litellm"
  instance            = google_sql_database_instance.this.name
  password_wo         = ephemeral.random_password.db.result
  password_wo_version = 1
  deletion_policy     = "ABANDON"
}

# a private address for the database, reachable from every gateway region
resource "google_compute_address" "db" {
  name         = "${var.name}-db"
  region       = var.primary_region
  subnetwork   = google_compute_subnetwork.this[var.primary_region].id
  address_type = "INTERNAL"
}

resource "google_compute_forwarding_rule" "db" {
  name                    = "${var.name}-db"
  region                  = var.primary_region
  network                 = google_compute_network.this.id
  ip_address              = google_compute_address.db.id
  load_balancing_scheme   = ""
  target                  = google_sql_database_instance.this.psc_service_attachment_link
  allow_psc_global_access = true
}

# apps and gateways use this name, so a promoted replica needs a DNS change and nothing else
resource "google_dns_managed_zone" "db" {
  name       = "${var.name}-db"
  dns_name   = "db.${var.name}.internal."
  visibility = "private"
  private_visibility_config {
    networks {
      network_url = google_compute_network.this.id
    }
  }
}

resource "google_dns_record_set" "db" {
  managed_zone = google_dns_managed_zone.db.name
  name         = "db.${var.name}.internal."
  type         = "A"
  ttl          = 30
  rrdatas      = [google_compute_address.db.address]
}

resource "google_redis_instance" "this" {
  for_each                = var.regions
  name                    = "${var.name}-${each.key}"
  region                  = each.key
  tier                    = var.size.redis_ha ? "STANDARD_HA" : "BASIC"
  memory_size_gb          = var.size.redis_gb
  redis_version           = "REDIS_7_2"
  authorized_network      = google_compute_network.this.id
  connect_mode            = "DIRECT_PEERING"
  auth_enabled            = true
  transit_encryption_mode = "SERVER_AUTHENTICATION"
}

# ---- secrets ---------------------------------------------------------------------------------------------------------
resource "google_secret_manager_secret" "this" {
  for_each  = toset(["database-url", "master-key", "salt-key"])
  secret_id = "${var.name}-${each.key}"
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

resource "google_secret_manager_secret_version" "database_url" {
  secret                 = google_secret_manager_secret.this["database-url"].id
  secret_data_wo         = "postgresql://litellm:${ephemeral.random_password.db.result}@db.${var.name}.internal:5432/litellm?sslmode=require"
  secret_data_wo_version = 1
  depends_on             = [google_sql_user.this]
}

resource "google_secret_manager_secret_version" "master_key" {
  secret                 = google_secret_manager_secret.this["master-key"].id
  secret_data_wo         = "sk-${ephemeral.random_password.master.result}"
  secret_data_wo_version = 1
}

# never change this one: it encrypts what LiteLLM keeps in the database
resource "google_secret_manager_secret_version" "salt_key" {
  secret                 = google_secret_manager_secret.this["salt-key"].id
  secret_data_wo         = "sk-${ephemeral.random_password.salt.result}"
  secret_data_wo_version = 1
}

resource "google_secret_manager_secret" "redis" {
  for_each  = var.regions
  secret_id = "${var.name}-redis-${each.key}"
  replication {
    user_managed {
      replicas {
        location = each.key
      }
    }
  }
}

resource "google_secret_manager_secret_version" "redis" {
  for_each               = var.regions
  secret                 = google_secret_manager_secret.redis[each.key].id
  secret_data_wo         = google_redis_instance.this[each.key].auth_string
  secret_data_wo_version = 1
}

# Redis's CA certificate: public, kept as a secret because Cloud Run mounts secrets as files
resource "google_secret_manager_secret" "redis_ca" {
  for_each  = var.regions
  secret_id = "${var.name}-redis-ca-${each.key}"
  replication {
    user_managed {
      replicas {
        location = each.key
      }
    }
  }
}

resource "google_secret_manager_secret_version" "redis_ca" {
  for_each    = var.regions
  secret      = google_secret_manager_secret.redis_ca[each.key].id
  secret_data = join("\n", [for c in google_redis_instance.this[each.key].server_ca_certs : c.cert])
}

# ---- a copy in the second region (db_replica): promoted by clouds/gcp/scripts/db_failover.sh ----------------------------
locals {
  replica_region = try([for r in keys(var.regions) : r if r != var.primary_region][0], null)
}

check "db_replica_region" {
  assert {
    condition     = !var.db_replica || local.replica_region != null
    error_message = "db_replica needs a second gateway region."
  }
}

resource "google_sql_database_instance" "replica" {
  count                = var.db_replica && local.replica_region != null ? 1 : 0
  name                 = "${var.name}-${random_id.db.hex}-copy"
  region               = local.replica_region
  database_version     = "POSTGRES_16"
  master_instance_name = google_sql_database_instance.this.name
  deletion_protection  = var.deletion_protection
  replica_configuration {
    failover_target = false
  }
  settings {
    edition                     = "ENTERPRISE"
    tier                        = var.size.db_tier
    availability_type           = "ZONAL"
    disk_autoresize             = true
    deletion_protection_enabled = var.deletion_protection
    ip_configuration {
      ipv4_enabled = false
      ssl_mode     = "ENCRYPTED_ONLY"
      psc_config {
        psc_enabled               = true
        allowed_consumer_projects = [var.project]
      }
    }
  }
}

resource "google_compute_address" "db_copy" {
  count        = length(google_sql_database_instance.replica)
  name         = "${var.name}-db-copy"
  region       = local.replica_region
  subnetwork   = google_compute_subnetwork.this[local.replica_region].id
  address_type = "INTERNAL"
}

resource "google_compute_forwarding_rule" "db_copy" {
  count                   = length(google_sql_database_instance.replica)
  name                    = "${var.name}-db-copy"
  region                  = local.replica_region
  network                 = google_compute_network.this.id
  ip_address              = google_compute_address.db_copy[0].id
  load_balancing_scheme   = ""
  target                  = google_sql_database_instance.replica[0].psc_service_attachment_link
  allow_psc_global_access = true
}

output "db_failover" {
  value = length(google_sql_database_instance.replica) == 0 ? null : {
    replica = google_sql_database_instance.replica[0].name
    address = google_compute_address.db_copy[0].address
    zone    = google_dns_managed_zone.db.name
    record  = google_dns_record_set.db.name
    gateway = var.project
    regions = keys(var.regions)
    service = "${var.name}-gateway"
  }
}
