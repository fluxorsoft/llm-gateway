#!/usr/bin/env bash
# After db_failover.sh, when the primary region is well again: copies the data from the promoted copy back into the
# primary database, points db.<name>.internal at the primary, restarts the gateways, and deletes the promoted copy. The
# next apply makes a new copy. Writes between the export and the restart are lost: a few seconds of spend.
#   terraform -chdir=clouds/gcp/platform output -json db_failover | clouds/gcp/scripts/db_failback.sh
# The output must be the one saved before the failover, or a fresh one: the plan refuses while the copy is promoted,
# but `terraform output` still works.
set -euo pipefail
in="$(cat)"
. "$(dirname "$0")/db_common.sh"
[ "$(get primary)" != null ] || { echo "the output has no primary: take it from a 1.1.0 or later platform" >&2; exit 1; }
project="$(get gateway)" copy="$(get replica)" primary="$(get primary)" db="$(get database)" user="$(get user)"
kind="$(gcloud sql instances describe "$copy" --project="$project" --format='value(instanceType)')"
[ "$kind" = CLOUD_SQL_INSTANCE ] || { echo "$copy is not promoted ($kind): nothing to fail back" >&2; exit 1; }
bucket="gs://${project}-${primary}-failback"
dump="$bucket/$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
sa() { gcloud sql instances describe "$1" --project="$project" --format='value(serviceAccountEmailAddress)'; }
echo "a short-lived bucket for the copy: $bucket" >&2
gcloud storage buckets describe "$bucket" > /dev/null 2>&1 ||
  gcloud storage buckets create "$bucket" --project="$project" --location="$(get bucket_region)" --uniform-bucket-level-access --quiet
gcloud storage buckets add-iam-policy-binding "$bucket" --member="serviceAccount:$(sa "$copy")" --role=roles/storage.objectAdmin --quiet > /dev/null
gcloud storage buckets add-iam-policy-binding "$bucket" --member="serviceAccount:$(sa "$primary")" --role=roles/storage.objectViewer --quiet > /dev/null
echo "exporting $db from $copy" >&2
gcloud sql export sql "$copy" "$dump" --database="$db" --clean --if-exists --project="$project" --quiet
echo "importing it into $primary" >&2
gcloud sql import sql "$primary" "$dump" --database="$db" --user="$user" --project="$project" --quiet
echo "pointing $(get record) at $(get primary_address)" >&2
gcloud dns record-sets update "$(get record)" --zone="$(get zone)" --type=A --ttl=30 --rrdatas="$(get primary_address)" --project="$project"
restart_gateways
echo "deleting the promoted copy $copy and the bucket" >&2
gcloud sql instances patch "$copy" --no-deletion-protection --project="$project" --quiet > /dev/null
gcloud sql instances delete "$copy" --project="$project" --quiet
gcloud storage rm --recursive "$bucket" --quiet
echo "done: run the checks as each team, then apply (it makes a new copy)" >&2
