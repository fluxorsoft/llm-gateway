"""The price check: the gateway's own cost figures against Google's bill, per team project, for the last full week.

  onedoorai pricecheck --usage <project.dataset> --billing <project.dataset.table> [--above 10]

Writes one line per project: {"message": "llm price check", "project", "gateway_usd", "billed_usd", "difference_percent", "level"}.
level is "drift" when the two differ by more than --above per cent, else "ok". The bill counts every Vertex AI use in the
project, including uses that do not pass the gateway (RAG Engine's own embeddings, for example), so a team that uses those
will always differ: the line says so, and the alert is for a change, not a perfect match.
"""
import json
import os
import sys
import urllib.request

GATEWAY = """
SELECT jsonPayload.llm_usage.project AS project, SUM(jsonPayload.llm_usage.cost_usd) AS usd
FROM `{usage}.run_googleapis_com_stdout`
WHERE jsonPayload.llm_usage.ok AND timestamp >= {start}
  AND timestamp < {end}
GROUP BY project
"""
BILLED = """
SELECT project.id AS project, SUM(cost / IFNULL(NULLIF(currency_conversion_rate, 0), 1)) AS usd
FROM `{billing}`
WHERE service.description = 'Vertex AI' AND project.id IN UNNEST(@projects)
  AND usage_start_time >= {start}
  AND usage_start_time < {end}
GROUP BY project
"""


WINDOWS = {  # last_week: the last full week (Monday to Sunday); last_day: the last 24 hours, for a first test
    "last_week": (f"TIMESTAMP(DATE_SUB(DATE_TRUNC(CURRENT_DATE(), WEEK(MONDAY)), INTERVAL 7 DAY))", "TIMESTAMP(DATE_TRUNC(CURRENT_DATE(), WEEK(MONDAY)))"),
    "last_day": ("TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)", "CURRENT_TIMESTAMP()"),
}


def compare(gateway, billed, above=10.0):
    """gateway, billed: {project: usd}. One line per project the gateway used."""
    out = []
    for project in sorted(gateway):
        g, b = float(gateway[project] or 0), float(billed.get(project) or 0)
        diff = 0.0 if g == b else round(100 * abs(b - g) / max(g, b), 1)
        out.append({"message": "llm price check", "project": project, "gateway_usd": round(g, 4), "billed_usd": round(b, 4),
                    "difference_percent": diff, "level": "drift" if diff > above else "ok",
                    "severity": "WARNING" if diff > above else "INFO"})
    return out


def _token():
    r = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                               headers={"Metadata-Flavor": "Google"})
    return json.load(urllib.request.urlopen(r, timeout=10))["access_token"]


def query(project, sql, params=None, token=_token):
    body = {"query": sql, "useLegacySql": False, "timeoutMs": 60000}
    if params:
        body["queryParameters"] = params
    r = urllib.request.Request(f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries", json.dumps(body).encode(),
                               {"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    a = json.load(urllib.request.urlopen(r, timeout=90))
    return {row["f"][0]["v"]: float(row["f"][1]["v"] or 0) for row in a.get("rows", [])}


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="onedoorai pricecheck")
    p.add_argument("--usage", required=True, help="the usage dataset, project.dataset")
    p.add_argument("--billing", required=True, help="the billing export table, project.dataset.table")
    p.add_argument("--above", type=float, default=float(os.environ.get("PRICE_CHECK_ABOVE", "10")))
    p.add_argument("--window", choices=sorted(WINDOWS), default=os.environ.get("PRICE_CHECK_WINDOW", "last_week"))
    a = p.parse_args(argv)
    project = a.usage.split(".")[0]
    start, end = WINDOWS[a.window]
    gateway = query(project, GATEWAY.format(usage=a.usage, start=start, end=end))
    if not gateway:
        print(json.dumps({"message": "llm price check", "level": "ok", "why": f"no gateway use in the window ({a.window})"}), flush=True)
        return 0
    billed = query(project, BILLED.format(billing=a.billing, start=start, end=end), [{"name": "projects", "parameterType": {"type": "ARRAY", "arrayType": {"type": "STRING"}},
                                                                "parameterValue": {"arrayValues": [{"value": p} for p in gateway]}}])
    for line in compare(gateway, billed, a.above):
        print(json.dumps(line), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
