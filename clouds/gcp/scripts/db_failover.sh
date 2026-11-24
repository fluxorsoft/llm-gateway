#!/usr/bin/env bash
# Makes the database copy the database: promotes it, points db.<name>.internal at it, and restarts the gateways so every
# connection is new. Recent writes not yet copied can be lost (the copy is asynchronous): a few seconds of spend.
#   terraform -chdir=clouds/gcp/platform output -json db_failover | clouds/gcp/scripts/db_failover.sh
# Afterwards every plan refuses until clouds/gcp/scripts/db_failback.sh has run (docs/OPERATIONS.md).
set -euo pipefail
in="$(cat)"
. "$(dirname "$0")/db_common.sh"
[ "$(get replica)" != null ] || { echo "no database copy: set db_replica = true first" >&2; exit 1; }
project="$(get gateway)"
echo "promoting $(get replica)" >&2
gcloud sql instances promote-replica "$(get replica)" --project="$project" --quiet
echo "pointing $(get record) at $(get address)" >&2
gcloud dns record-sets update "$(get record)" --zone="$(get zone)" --type=A --ttl=30 --rrdatas="$(get address)" --project="$project"
restart_gateways
echo "done: check the gateways' readiness, then run the checks as each team" >&2
