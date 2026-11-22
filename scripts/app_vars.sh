#!/usr/bin/env bash
# The example apps' Terraform variables, from the platform's outputs (terraform output -json on stdin).
#   IMAGE=... PROJECT=... REPO_LOCATION=europe NAME=llmgw MEMBERS='["user:a@example.com"]' scripts/app_vars.sh < outputs.json
set -euo pipefail
: "${IMAGE:?}" "${PROJECT:?}" "${REPO_LOCATION:=europe}" "${NAME:=llmgw}" "${MEMBERS:=[]}"
jq --arg image "$IMAGE" --arg project "$PROJECT" --arg loc "$REPO_LOCATION" --arg name "$NAME" --argjson members "$MEMBERS" '{
  gateway_project: $project, name: $name, image: $image, address: .address.value, ca_pem: .ca_pem.value,
  repository: {location: $loc, name: $name}, members: $members, default_urls: .gateway_default_urls.value,
  apps: {
    orders: (.teams.value.orders + {model: "orders-fast", other_model: "support-chat"}),
    support: (.teams.value.support + {model: "support-chat", other_model: "orders-fast"})
  }}'
