# The whole platform planned with pretend providers (no cloud access): every switch off, then every switch on. It finds
# what `validate` cannot: type errors that only appear once the settings are read.
mock_provider "google" {
  mock_data "google_project" {
    defaults = { number = "123456789012" }
  }
  mock_data "google_bigquery_default_service_account" {
    defaults = { email = "bq-123456789012@bigquery-encryption.iam.gserviceaccount.com" }
  }
}

variables {
  project = "example-project"
  images = {
    gateway = "x@sha256:0000000000000000000000000000000000000000000000000000000000000000"
    runner  = "x@sha256:0000000000000000000000000000000000000000000000000000000000000000"
    app     = "x@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  }
  deletion_protection = false
}

run "every_switch_off" {
  command = plan
  variables {
    settings_dir = "../../../onboarding/settings"
  }
}

run "every_switch_on" {
  command = plan
  variables {
    settings_dir       = "../../../core/settings/testdata/people"
    developer_door     = { always_on = "primary" }
    budget_alerts      = true
    spend_log_days     = 30
    db_replica         = true
    price_check        = { billing_export = "billing-project.billing_export.gcp_billing_export_v1_X" }
    audit_record       = { days = 30 }
    check_tool_traffic = true
    canary             = { region = "europe-west1", gateway = "y@sha256:1111111111111111111111111111111111111111111111111111111111111111" }
    cmek_keys = {
      europe-west2 = "projects/k/locations/europe-west2/keyRings/r/cryptoKeys/c"
      europe-west1 = "projects/k/locations/europe-west1/keyRings/r/cryptoKeys/c"
    }
    vpc_sc = { policy = "accessPolicies/1", identities = ["serviceAccount:deployer@example-project.iam.gserviceaccount.com"] }
  }
}
