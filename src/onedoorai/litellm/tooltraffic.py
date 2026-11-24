"""Checking tool traffic on the way (check_tool_traffic), and the calls-per-task cap.

LiteLLM's Model Armor guardrail does not run on tool-server (MCP) traffic, even with pre_mcp_call and post_mcp_call set:
it accepts the setting and does nothing (found 24 November 2026, LiteLLM 1.110.3). So the request filter does it: a tool
call's arguments are read by Model Armor before the call goes on, and the tool's answer is held and read before it goes
back. Blocked, not fully checked, or not checkable: the call is refused. No check, no call.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

from .text import MAX_TEXT, incomplete, words

_token = {"value": "", "until": 0.0}
_lock = threading.Lock()


def _access_token():
    with _lock:
        if time.time() > _token["until"]:
            host = os.environ.get("GCE_METADATA_HOST", "metadata.google.internal")
            r = urllib.request.Request(f"http://{host}/computeMetadata/v1/instance/service-accounts/default/token",
                                       headers={"Metadata-Flavor": "Google"})
            got = json.load(urllib.request.urlopen(r, timeout=10))
            _token.update(value=got["access_token"], until=time.time() + min(int(got.get("expires_in", 600)), 3000) - 60)
        return _token["value"]


def check(text, answer=False, post=None):
    """Why this text may not pass, or None. post(url, body) -> dict lets tests stand in for Model Armor."""
    if not text.strip():
        return None
    if len(text) > MAX_TEXT:
        return f"too long to be checked whole ({len(text)} characters)"
    location, project, template = os.environ["ARMOR_LOCATION"], os.environ["ARMOR_PROJECT"], os.environ["ARMOR_TEMPLATE"]
    base = os.environ.get("ARMOR_ENDPOINT") or f"https://modelarmor.{location}.rep.googleapis.com"
    url = f"{base}/v1/projects/{project}/locations/{location}/templates/{template}:" + ("sanitizeModelResponse" if answer else "sanitizeUserPrompt")
    body = {"modelResponseData" if answer else "userPromptData": {"text": text}}
    try:
        if post:
            got = post(url, body)
        else:
            r = urllib.request.Request(url, json.dumps(body).encode(), {"Authorization": "Bearer " + _access_token(), "Content-Type": "application/json"})
            got = json.load(urllib.request.urlopen(r, timeout=30))
    except Exception as e:
        return f"could not be checked ({type(e).__name__})"
    why = incomplete(got)
    if why:
        return f"not fully checked ({why})"
    if (got.get("sanitizationResult") or {}).get("filterMatchState") == "MATCH_FOUND":
        return "blocked by Model Armor"
    return None


def call_text(data):
    """A tools/call request's words: the tool's name and its arguments."""
    params = data.get("params") or {}
    return "\n".join([str(params.get("name") or "")] + [w for w in words(params.get("arguments")) if w.strip()])


def answer_text(body, content_type=""):
    """A tool's answer's words, from JSON or from server-sent events."""
    payloads = []
    text = body.decode(errors="replace")
    if "event-stream" in content_type:
        payloads = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
    else:
        payloads = [text]
    out = []
    for p in payloads:
        try:
            msg = json.loads(p)
        except ValueError:
            continue
        result = msg.get("result") or {}
        out += [w for w in words(result.get("content")) if w.strip() and w != "text"]
        if result.get("structuredContent") is not None:
            out += [w for w in words(result["structuredContent"]) if w.strip()]
    return "\n".join(out)


def rpc_error(request_id, why):
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "refused: " + why}}).encode()


# ---- the calls-per-task cap ---------------------------------------------------------------------------------------------
class Counter:
    """Counts an agent's calls per task id for a day: in this region's Redis, or in this instance if Redis is away."""

    def __init__(self):
        self.local, self.redis = {}, None
        if os.environ.get("REDIS_HOST"):
            try:
                import redis

                self.redis = redis.Redis(host=os.environ["REDIS_HOST"], port=int(os.environ.get("REDIS_PORT", "6379")),
                                         password=os.environ.get("REDIS_PASSWORD") or None,
                                         ssl=os.environ.get("REDIS_SSL", "").lower() == "true",
                                         ssl_ca_certs=os.environ.get("REDIS_SSL_CA_CERTS") or None, socket_timeout=2)
            except Exception:
                self.redis = None

    def add(self, key):
        if self.redis is not None:
            try:
                n = self.redis.incr("llmgw:task:" + key)
                if n == 1:
                    self.redis.expire("llmgw:task:" + key, 86400)
                return int(n)
            except Exception:
                pass
        self.local[key] = self.local.get(key, 0) + 1
        if len(self.local) > 10000:
            self.local.clear()
        return self.local[key]


_counter = None


def counter():
    global _counter
    if _counter is None:
        _counter = Counter()
    return _counter
