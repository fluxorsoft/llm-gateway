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
  encryption_key_name = lookup(var.cmek_keys, var.primary_region, null)
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
  customer_managed_key    = lookup(var.cmek_keys, each.key, null)
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
          dynamic "customer_managed_encryption" {
            for_each = contains(keys(var.cmek_keys), replicas.key) ? [var.cmek_keys[replicas.key]] : []
            content {
              kms_key_name = customer_managed_encryption.value
            }
          }
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
        dynamic "customer_managed_encryption" {
          for_each = contains(keys(var.cmek_keys), each.key) ? [var.cmek_keys[each.key]] : []
          content {
            kms_key_name = customer_managed_encryption.value
          }
        }
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
        dynamic "customer_managed_encryption" {
          for_each = contains(keys(var.cmek_keys), each.key) ? [var.cmek_keys[each.key]] : []
          content {
            kms_key_name = customer_managed_encryption.value
          }
        }
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

# What Google says each database is now: after db_failover.sh the copy is a database of its own, and an apply would
# replace it (the live data) with a new copy of the old one. Refused until db_failback.sh has run.
data "google_sql_database_instances" "now" {
  count   = var.db_replica ? 1 : 0
  project = var.project
}

locals {
  copy_promoted = var.db_replica ? contains([
    for i in data.google_sql_database_instances.now[0].instances : i.name if i.instance_type == "CLOUD_SQL_INSTANCE"
  ], "${var.name}-${random_id.db.hex}-copy") : false
}

resource "google_sql_database_instance" "replica" {
  count                = var.db_replica && local.replica_region != null ? 1 : 0
  name                 = "${var.name}-${random_id.db.hex}-copy"
  region               = local.replica_region
  database_version     = "POSTGRES_16"
  master_instance_name = google_sql_database_instance.this.name
  encryption_key_name  = lookup(var.cmek_keys, local.replica_region, null)
  deletion_protection  = var.deletion_protection
  replica_configuration {
    failover_target = false
  }
  lifecycle {
    precondition {
      condition     = !local.copy_promoted
      error_message = "The database copy was promoted (db_failover.sh) and is the live database: an apply would replace it. Run clouds/gcp/scripts/db_failback.sh first (docs/OPERATIONS.md, \"A database copy\")."
    }
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
  lifecycle {
    # a new copy (after a fail-back) has a new attachment; said at plan, or the provider changes its mind during apply
    replace_triggered_by = [google_sql_database_instance.replica[0].psc_service_attachment_link]
  }
}

output "db_failover" {
  value = length(google_sql_database_instance.replica) == 0 ? null : {
    replica         = google_sql_database_instance.replica[0].name
    address         = google_compute_address.db_copy[0].address
    primary         = google_sql_database_instance.this.name
    primary_address = google_compute_address.db.address
    database        = google_sql_database.this.name
    user            = google_sql_user.this.name
    bucket_region   = var.primary_region
    zone            = google_dns_managed_zone.db.name
    record          = google_dns_record_set.db.name
    gateway         = var.project
    regions         = keys(var.regions)
    service         = "${var.name}-gateway"
  }
}

# ---- your own keys (cmek_keys): Google's service agents may use them -------------------------------------------------------
# Cloud SQL's and Secret Manager's agents exist once made: clouds/gcp/scripts/bootstrap.sh does it when CMEK=1.
data "google_bigquery_default_service_account" "this" {
  count = length(var.cmek_keys) == 0 ? 0 : 1
}

locals {
  key_users = length(var.cmek_keys) == 0 ? {} : merge([for r, k in var.cmek_keys : {
    "${r}/sql"     = { key = k, member = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-cloud-sql.iam.gserviceaccount.com" }
    "${r}/redis"   = { key = k, member = "serviceAccount:service-${data.google_project.this.number}@cloud-redis.iam.gserviceaccount.com" }
    "${r}/secrets" = { key = k, member = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-secretmanager.iam.gserviceaccount.com" }
    "${r}/bq"      = { key = k, member = "serviceAccount:${data.google_bigquery_default_service_account.this[0].email}" }
  }]...)
}

resource "google_kms_crypto_key_iam_member" "agents" {
  for_each      = local.key_users
  crypto_key_id = each.value.key
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value.member
}
