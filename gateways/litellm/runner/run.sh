#!/bin/sh
# MODE=plan (the default): fails when LiteLLM differs from the settings. MODE=apply: makes it match.
set -u
fail() {
  echo "{\"severity\":\"ERROR\",\"message\":\"config run failed\",\"mode\":\"${MODE:-plan}\",\"why\":\"$1\"}"
  exit 1
}
cat > /tmp/terraformrc <<EOF
provider_installation {
  filesystem_mirror { path = "/providers" }
}
EOF
export TF_CLI_CONFIG_FILE=/tmp/terraformrc
python -m onedoorai admin &
for i in $(seq 1 60); do
  wget -q -O /dev/null http://127.0.0.1:9000/health/liveliness && break
  [ "$i" = 60 ] && fail "the admin service did not answer"
  sleep 3
done
terraform init -input=false -no-color -backend-config="bucket=${STATE_BUCKET}" -backend-config="prefix=config" > /tmp/init.log 2>&1 \
  || { cat /tmp/init.log; fail "terraform init"; }
keys() {
  terraform output -json keys > /tmp/wanted.json || fail "terraform output"
  python -m onedoorai keys --wanted /tmp/wanted.json "$@"
}
case "${MODE:-plan}" in
  apply)
    terraform apply -input=false -no-color -auto-approve || fail "terraform apply"
    keys --apply || fail "the key step"
    ;;
  pricecheck) python -m onedoorai pricecheck --usage "$USAGE_DATASET" --billing "$BILLING_EXPORT" || fail "the price check" ;;
  budgets) python -m onedoorai budgets || fail "the budget levels" ;;
  probe) keys --probe || fail "the upgrade is refused: this LiteLLM no longer accepts the keys' way of making values" ;;
  destroy) terraform destroy -input=false -no-color -auto-approve || fail "terraform destroy" ;;
  plan)
    terraform plan -input=false -no-color -detailed-exitcode
    code=$?
    [ "$code" = 2 ] && fail "LiteLLM differs from the settings"
    [ "$code" = 0 ] || fail "terraform plan"
    keys
    code=$?
    [ "$code" = 2 ] && fail "the keys differ from the settings"
    [ "$code" = 0 ] || fail "the key step"
    ;;
  *) fail "MODE is plan, apply, budgets, pricecheck, probe or destroy" ;;
esac
