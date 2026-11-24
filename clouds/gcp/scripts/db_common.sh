# Shared by db_failover.sh and db_failback.sh: reads the db_failover output, restarts the gateways.
py="$(command -v python3 || command -v python || true)"
get() {  # jq if present, else Python: one less thing to install in a hurry
  if command -v jq > /dev/null; then jq -r ".$1" <<< "$in"; else "$py" -c "import json,sys; v=json.load(sys.stdin).get(sys.argv[1]); print('null' if v is None else v)" "$1" <<< "$in"; fi | tr -d '\r'
}
regions() { if command -v jq > /dev/null; then jq -r '.regions[]' <<< "$in"; else "$py" -c "import json,sys; print('\n'.join(json.load(sys.stdin)['regions']))" <<< "$in"; fi | tr -d '\r'; }

# A new revision, so every connection is new. Through gcloud; if that fails (some gcloud releases refuse their own
# regional Cloud Run address), through the Cloud Run API directly.
restart_gateways() {
  local project="$(get gateway)" service="$(get service)" stamp region
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  for region in $(regions); do
    echo "restarting the gateway in $region" >&2
    gcloud run services update "$service" --region="$region" --project="$project" \
      --update-env-vars="DB_FAILOVER_AT=$stamp" --quiet > /dev/null 2>&1 && continue
    echo "  gcloud refused; using the Cloud Run API" >&2
    TOKEN="$(gcloud auth print-access-token)" "$py" - "$project" "$region" "$service" "$stamp" <<'PY'
import json, os, sys, time, urllib.request
project, region, service, stamp = sys.argv[1:]
url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/services/{service}"
def call(method, u, body=None):
    r = urllib.request.Request(u, json.dumps(body).encode() if body else None,
                               {"Authorization": "Bearer " + os.environ["TOKEN"].strip(), "Content-Type": "application/json"}, method=method)
    return json.load(urllib.request.urlopen(r, timeout=120))
containers = call("GET", url)["template"]["containers"]
c = containers[0]
c["env"] = [e for e in c.get("env", []) if e["name"] != "DB_FAILOVER_AT"] + [{"name": "DB_FAILOVER_AT", "value": stamp}]
op = call("PATCH", url + "?updateMask=template.containers", {"template": {"containers": containers}})
while not op.get("done"):
    time.sleep(5)
    op = call("GET", "https://run.googleapis.com/v2/" + op["name"])
if "error" in op:
    sys.exit(f"  {region}: {op['error']}")
PY
  done
}
