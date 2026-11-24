#!/usr/bin/env bash
# Verifies LiteLLM's signature, builds the three images, pushes them and prints them by digest (JSON).
#   REPO=europe-docker.pkg.dev/<project>/<name> SETTINGS_DIR=settings/example scripts/images.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${REPO:?}" "${SETTINGS_DIR:=settings/example}"
base="$(tr -d '\r\n' < gateways/litellm/LITELLM_IMAGE)"
cosign verify "$base" \
  --certificate-identity="https://github.com/BerriAI/project-releaser/.github/workflows/release.yml@refs/heads/main" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com" > /dev/null
echo "signature verified: $base" >&2
tag="$(git rev-parse --short HEAD)-${GITHUB_RUN_ID:-local}"
build() {
  case "$1" in
    gateway) docker build -q --build-arg "LITELLM_IMAGE=$base" -t "$REPO/gateway:$tag" -f gateways/litellm/gateway/Dockerfile . ;;
    runner) docker build -q --build-arg "SETTINGS_DIR=$SETTINGS_DIR" -t "$REPO/runner:$tag" -f gateways/litellm/runner/Dockerfile . ;;
    app) docker build -q -t "$REPO/app:$tag" apps ;;
  esac
}
out="${KEEP:-}" # images to keep as they are (JSON)
[ -n "$out" ] || out="{}"
for i in ${ONLY:-gateway runner app}; do
  build "$i" >&2
  docker push -q "$REPO/$i:$tag" >&2
  out="$(jq -c --arg k "$i" --arg v "$(docker inspect --format '{{index .RepoDigests 0}}' "$REPO/$i:$tag")" '.[$k] = $v' <<< "$out")"
done
echo "$out"
