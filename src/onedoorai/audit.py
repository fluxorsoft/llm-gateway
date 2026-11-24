"""The admin record: once a day, what LiteLLM is set to (teams, models, fallbacks, key names and limits; never a key's
value), written to a bucket that keeps every object unchanged for its retention period. Each day's record carries the
previous day's fingerprint, so a missing or edited day shows.

  onedoorai audit --bucket <bucket>        (in the runner, through `onedoorai admin`)
"""
import datetime
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from .keys import Admin

SECRET_FIELDS = {"token", "key", "api_key", "vertex_credentials", "aws_secret_access_key", "master_key", "litellm_credential_name"}


def scrub(value):
    """The same structure without anything that could be a secret."""
    if isinstance(value, dict):
        return {k: ("(hidden)" if k in SECRET_FIELDS and v else scrub(v)) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def canonical(settings):
    return json.dumps(scrub(settings), sort_keys=True, separators=(",", ":"), default=str)


def record(settings, previous_digest, day):
    body = canonical(settings)
    digest = hashlib.sha256(((previous_digest or "") + body).encode()).hexdigest()
    return {"day": day, "previous": previous_digest, "digest": digest, "settings": json.loads(body)}


def read_settings(admin):
    keys = []
    for team in admin.call("GET", "/team/list"):
        for alias, k in sorted(admin.keys(team["team_id"]).items()):
            keys.append({f: k.get(f) for f in ("key_alias", "team_id", "user_id", "models", "max_budget", "budget_duration",
                                                "rpm_limit", "tpm_limit", "expires", "blocked")})
    teams = [{f: t.get(f) for f in ("team_alias", "team_id", "models", "max_budget", "budget_duration", "rpm_limit",
                                     "tpm_limit", "blocked", "metadata", "model_aliases")} for t in admin.call("GET", "/team/list")]
    models = admin.call("GET", "/model/info").get("data", [])
    return {"teams": teams, "keys": keys, "models": models}


def _token():
    r = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                               headers={"Metadata-Flavor": "Google"})
    return json.load(urllib.request.urlopen(r, timeout=10))["access_token"]


def latest(bucket, token):
    q = urllib.parse.urlencode({"prefix": "audit/", "fields": "items(name)"})
    r = urllib.request.Request(f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?{q}", headers={"Authorization": "Bearer " + token})
    names = sorted(i["name"] for i in json.load(urllib.request.urlopen(r, timeout=30)).get("items", []))
    if not names:
        return None
    r = urllib.request.Request(f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{urllib.parse.quote(names[-1], safe='')}?alt=media",
                               headers={"Authorization": "Bearer " + token})
    return json.load(urllib.request.urlopen(r, timeout=30))


def write(bucket, name, body, token):
    r = urllib.request.Request(f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o?uploadType=media&name={urllib.parse.quote(name, safe='')}&ifGenerationMatch=0",
                               body.encode(), {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    urllib.request.urlopen(r, timeout=30)


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="onedoorai audit")
    p.add_argument("--bucket", default=os.environ.get("AUDIT_BUCKET"), required=not os.environ.get("AUDIT_BUCKET"))
    a = p.parse_args(argv)
    master = os.environ.get("TF_VAR_master_key") or os.environ.get("LITELLM_MASTER_KEY")
    admin = Admin(os.environ.get("ADMIN_PROXY", "http://127.0.0.1:9000"), master)
    token = _token()
    before = latest(a.bucket, token)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    rec = record(read_settings(admin), before and before.get("digest"), now)
    changed = before is None or before.get("settings") != rec["settings"]
    try:
        write(a.bucket, f"audit/{now}.json", json.dumps(rec, indent=1), token)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"the record could not be written: {e.code}") from None
    print(json.dumps({"message": "llm audit", "digest": rec["digest"], "previous": rec["previous"], "changed": changed}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
