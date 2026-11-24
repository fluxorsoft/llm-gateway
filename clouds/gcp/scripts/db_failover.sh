#!/usr/bin/env bash
# Makes the database copy the database: promotes it, points db.<name>.internal at it, and restarts the gateways so every
# connection is new. Recent writes not yet copied can be lost (the copy is asynchronous): a few seconds of spend.
#   terraform -chdir=clouds/gcp/platform output -json db_failover | clouds/gcp/scripts/db_failover.sh
# After it, Terraform no longer describes the database: see the README, "A database copy", before the next apply.
set -euo pipefail
in="$(cat)"
get() { jq -r ".$1" <<< "$in"; }
[ "$(get replica)" != null ] || { echo "no database copy: set db_replica = true first" >&2; exit 1; }
project="$(get gateway)"
echo "promoting $(get replica)" >&2
gcloud sql instances promote-replica "$(get replica)" --project="$project" --quiet
echo "pointing $(get record) at $(get address)" >&2
gcloud dns record-sets update "$(get record)" --zone="$(get zone)" --type=A --ttl=30 --rrdatas="$(get address)" --project="$project"
for region in $(jq -r '.regions[]' <<< "$in"); do
  echo "restarting the gateway in $region" >&2
  gcloud run services update "$(get service)" --region="$region" --project="$project" \
    --update-env-vars="DB_FAILOVER_AT=$(date -u +%Y%m%dT%H%M%SZ)" --quiet > /dev/null
done
echo "done: check the gateways' readiness, then run the checks as each team" >&2
