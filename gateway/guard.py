"""Starts the LiteLLM proxy. On a gateway (GATEWAY_ROLE=gateway) it puts a request filter in front of it.

The filter is an ASGI middleware, so it sees the raw request before LiteLLM does:

- only the inference paths exist (no UI, no admin API, no pass-through routes);
- a request carries only the fields below, so a caller cannot choose a project, a location, a key or a base URL;
- headers are kept by name, so LiteLLM's control headers never reach it;
- the body passed on is rebuilt from the parsed one: the filter and LiteLLM can never read it differently.

A LiteLLM hook cannot do this: LiteLLM has already changed the body when a hook sees it. The admin service
(GATEWAY_ROLE=admin) is not filtered; Cloud Run IAM protects it.
"""
import json
import os

CHAT = {"model", "messages", "stream", "stream_options", "temperature", "top_p", "max_tokens",
        "max_completion_tokens", "stop", "n", "seed", "tools", "tool_choice", "parallel_tool_calls",
        "response_format", "presence_penalty", "frequency_penalty", "reasoning_effort", "user"}
EMBEDDINGS = {"model", "input", "encoding_format", "dimensions", "user"}

# (method, path): the body fields allowed, or None when there is no body
PATHS = {("POST", "/v1/chat/completions"): CHAT,
         ("POST", "/v1/embeddings"): EMBEDDINGS,
         ("GET", "/v1/models"): None,
         ("GET", "/health/readiness"): None,
         ("GET", "/health/liveliness"): None}

KEEP_HEADERS = {b"authorization", b"accept", b"host", b"user-agent", b"x-request-id", b"traceparent",
                b"x-cloud-trace-context"}
MAX_BODY = int(os.environ.get("MAX_BODY_BYTES", "10000000"))


class Filter:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":  # no websockets
            return await send({"type": "websocket.close", "code": 1008})
        fields = PATHS.get((scope["method"], scope["path"]), False)
        if fields is False:
            return await refuse(send, 404, "not found")
        if scope.get("query_string"):
            return await refuse(send, 400, "query parameters are not allowed")
        if any(k == b"content-encoding" for k, _ in scope["headers"]):
            return await refuse(send, 415, "compressed bodies are not allowed")
        headers = [(k, v) for k, v in scope["headers"] if k in KEEP_HEADERS]

        body = b""
        if fields is not None:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body += message.get("body", b"")
                if len(body) > MAX_BODY:
                    return await refuse(send, 413, "the body is too large")
                if not message.get("more_body"):
                    break
            try:
                data = json.loads(body)
            except ValueError:
                data = None
            if not isinstance(data, dict):
                return await refuse(send, 400, "the body must be a JSON object")
            bad = sorted(set(data) - fields)
            if bad:
                return await refuse(send, 400, "fields not allowed: " + ", ".join(bad)[:300])
            body = json.dumps(data).encode()
            headers += [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]

        sent = False

        async def again():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app({**scope, "headers": headers}, again, send)


async def refuse(send, code, why):
    print(json.dumps({"severity": "WARNING", "message": "gateway refused", "refused": {"code": code, "why": why}}),
          flush=True)
    body = json.dumps({"error": {"message": why, "type": "gateway_refused", "code": str(code)}}).encode()
    await send({"type": "http.response.start", "status": code,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def main():
    role = os.environ.get("GATEWAY_ROLE", "gateway")
    if role not in ("gateway", "admin"):
        raise SystemExit(f"GATEWAY_ROLE is {role!r}: use gateway or admin")
    from litellm.proxy.proxy_server import app

    if role == "gateway":
        app.add_middleware(Filter)
    from litellm import run_server

    run_server()


if __name__ == "__main__":
    main()
