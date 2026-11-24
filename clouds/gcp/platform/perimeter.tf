# A VPC Service Controls perimeter (vpc_sc) around the gateway project and every team project: data in the listed services
# cannot be read from outside it. Dry run first (enforce = false): violations are logged and nothing is blocked; read them in
# Logging (protoPayload.metadata.dryRun=true), add the identities that need to come in, then enforce. Off unless vpc_sc is set.
data "google_project" "team" {
  for_each   = var.vpc_sc == null ? toset([]) : toset([for t in module.settings.teams : t.project])
  project_id = each.key
}

locals {
  perimeter_projects = var.vpc_sc == null ? [] : concat(["projects/${data.google_project.this.number}"], [for t in data.google_project.team : "projects/${t.number}"])
}

resource "google_access_context_manager_service_perimeter" "this" {
  count                     = var.vpc_sc == null ? 0 : 1
  parent                    = var.vpc_sc.policy
  name                      = "${var.vpc_sc.policy}/servicePerimeters/${replace(var.name, "-", "_")}_gateway"
  title                     = "${var.name} gateway"
  perimeter_type            = "PERIMETER_TYPE_REGULAR"
  use_explicit_dry_run_spec = !var.vpc_sc.enforce

  dynamic "spec" {
    for_each = var.vpc_sc.enforce ? [] : [1]
    content {
      resources           = local.perimeter_projects
      restricted_services = var.vpc_sc.services
      dynamic "ingress_policies" {
        for_each = length(var.vpc_sc.identities) == 0 ? [] : [1]
        content {
          ingress_from {
            identities = var.vpc_sc.identities
            sources {
              access_level = "*"
            }
          }
          ingress_to {
            resources = ["*"]
            operations {
              service_name = "*"
            }
          }
        }
      }
    }
  }

  dynamic "status" {
    for_each = var.vpc_sc.enforce ? [1] : []
    content {
      resources           = local.perimeter_projects
      restricted_services = var.vpc_sc.services
      dynamic "ingress_policies" {
        for_each = length(var.vpc_sc.identities) == 0 ? [] : [1]
        content {
          ingress_from {
            identities = var.vpc_sc.identities
            sources {
              access_level = "*"
            }
          }
          ingress_to {
            resources = ["*"]
            operations {
              service_name = "*"
            }
          }
        }
      }
    }
  }
}
