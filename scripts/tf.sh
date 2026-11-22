#!/usr/bin/env bash
# Terraform in Docker: scripts/tf.sh <folder> <terraform arguments>. Set GOOGLE_OAUTH_ACCESS_TOKEN for cloud work.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; dir="$1"; shift
command -v cygpath >/dev/null && root="$(cygpath -m "$root")"
args=(-e CHECKPOINT_DISABLE=1 -e TF_IN_AUTOMATION=1)
[ -n "${CA_BUNDLE:-}" ] && args+=(-v "$CA_BUNDLE:/ca.pem:ro" -e SSL_CERT_FILE=/ca.pem)
[ -n "${GOOGLE_OAUTH_ACCESS_TOKEN:-}" ] && args+=(-e GOOGLE_OAUTH_ACCESS_TOKEN)
for v in $(env | grep -o '^TF_VAR_[a-z_]*' || true); do args+=(-e "$v"); done
MSYS_NO_PATHCONV=1 docker run --rm -v "$root:/repo" -w "/repo/$dir" "${args[@]}" hashicorp/terraform:1.16.5 "$@"
