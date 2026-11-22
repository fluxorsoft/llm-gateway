"""The whole client: plain REST to the gateway, standard library only. Copy it, or use any OpenAI-compatible SDK."""
import json
import os
import ssl
import time
import urllib.error
import urllib.request

URL = os.environ["LLM_URL"].rstrip("/")
CONTEXT = ssl.create_default_context()
if os.environ.get("LLM_CA_PEM"):  # a private certificate authority
    CONTEXT.load_verify_locations(cadata=os.environ["LLM_CA_PEM"])


class Refused(Exception):
    """Not worth retrying: a blocked prompt, a spent budget, a wrong key or model."""


def post(body, timeout=150):
    """Answers the open response. Retries a busy or failing gateway, with a pause; never a refusal."""
    request = urllib.request.Request(URL + "/v1/chat/completions", json.dumps(body).encode(), method="POST", headers={
        "Authorization": "Bearer " + os.environ["LLM_KEY"].strip(), "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            return urllib.request.urlopen(request, timeout=timeout, context=CONTEXT)
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            if e.code not in (429, 500, 502, 503, 504) or attempt == 2:
                try:
                    text = json.loads(text)["error"]["message"]
                except (ValueError, KeyError, TypeError):
                    pass
                raise Refused(f"{e.code}: {str(text)[:300]}") from None
        except OSError:
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def ask(model, messages, **options):
    """One answer, and where it came from."""
    started = time.time()
    with post({"model": model, "messages": messages, **options}) as r:
        data, headers = json.load(r), r.headers
    base = headers.get("x-litellm-model-api-base") or ""
    parts = base.split("/")
    return data["choices"][0]["message"]["content"], {
        "project": parts[parts.index("projects") + 1] if "projects" in parts else "",
        "location": parts[parts.index("locations") + 1] if "locations" in parts else "",
        "fallbacks": headers.get("x-litellm-attempted-fallbacks") or "0",
        "cost_usd": headers.get("x-litellm-response-cost") or "",
        "seconds": round(time.time() - started, 1)}


def stream(model, messages, **options):
    """Yields the text as it arrives. An error in the stream is raised: the answer is incomplete."""
    with post({"model": model, "messages": messages, "stream": True, **options}) as r:
        for line in r:
            if not line.startswith(b"data: ") or line.strip() == b"data: [DONE]":
                continue
            event = json.loads(line[6:])
            if event.get("error"):
                raise Refused(str(event["error"].get("message", event["error"]))[:300])
            for choice in event.get("choices", []):
                yield choice["delta"].get("content") or ""
