"""One structured log line per call: who, which model, where, how many tokens, what it cost. Never the prompt or the
answer. Cloud Logging keeps it, and a log sink copies it to BigQuery for chargeback.

The fields are the contract (docs/CONTRACT.md): any cloud and any gateway writes the same ones. Version 2 adds cloud,
gateway, door and task; version 1 readers ignore them.
"""
import json
import os
import re

from litellm.integrations.custom_logger import CustomLogger


def record(kwargs, ok):
    p = kwargs.get("standard_logging_object") or {}
    meta, hidden = p.get("metadata") or {}, p.get("hidden_params") or {}
    base = str(p.get("api_base") or hidden.get("api_base") or "")
    where = re.search(r"/projects/([^/]+)/locations/([^/]+)/", base)
    error = p.get("error_information") or {}
    return {
        "schema": 2,
        "cloud": os.environ.get("GATEWAY_CLOUD", "gcp"),
        "gateway": "litellm",
        "door": os.environ.get("GATEWAY_DOOR", "apps"),
        "task": (meta.get("requester_metadata") or {}).get("task"),
        "call_id": p.get("id"),
        "ok": ok,
        "team": meta.get("user_api_key_team_alias"),
        "key": meta.get("user_api_key_alias"),
        "name": p.get("model_group"),
        "model": p.get("model"),
        "project": where.group(1) if where else None,
        "location": where.group(2) if where else None,
        "input_tokens": p.get("prompt_tokens"),
        "output_tokens": p.get("completion_tokens"),
        "cost_usd": p.get("response_cost"),
        "seconds": p.get("response_time"),
        "error": error.get("error_class"),
        "error_code": str(error.get("error_code") or "") or None,
    }


class Usage(CustomLogger):
    def write(self, kwargs, ok):
        try:
            line = {"severity": "INFO" if ok else "ERROR", "message": "llm usage", "llm_usage": record(kwargs, ok)}
        except Exception as e:  # a logging fault must not fail a call
            line = {"severity": "ERROR", "message": "llm usage record failed", "why": type(e).__name__}
        print(json.dumps(line), flush=True)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self.write(kwargs, True)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self.write(kwargs, False)


usage = Usage()
