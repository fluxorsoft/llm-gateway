"""Starts the LiteLLM proxy. On a gateway (GATEWAY_ROLE=gateway) it puts a request filter in front of it.

The filter is an ASGI middleware, so it sees the raw request before LiteLLM does:

- only the inference paths exist (no UI, no admin API, no pass-through routes);
- a request carries only the fields below, so a caller cannot choose a project, a location, a key or a base URL;
- messages are text in plain shapes, and tools are the app's own functions: a provider's tools (web search, opening an
  address, running code) would act from the provider's side, where this network's walls do not reach;
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


ROLES = {"system", "developer", "user", "assistant", "tool"}


def only(value, *fields):
    return isinstance(value, dict) and not set(value) - set(fields)


def text(value):
    return isinstance(value, str) or (isinstance(value, list) and all(
        only(p, "type", "text") and p.get("type") == "text" and isinstance(p.get("text"), str) for p in value))


def function_call(c):
    return (only(c, "id", "type", "function", "index") and c.get("type", "function") == "function"
            and only(c.get("function"), "name", "arguments")
            and all(isinstance(v, str) for v in c["function"].values()))


def shape(data):
    """What is wrong with the values of a request whose fields are allowed, or None."""
    messages = data.get("messages")
    if messages is not None:
        if not isinstance(messages, list) or not messages:
            return "messages must be a list"
        for m in messages:
            if not only(m, "role", "content", "name", "tool_calls", "tool_call_id"):
                return "a message has only role, content, name, tool_calls and tool_call_id"
            if m.get("role") not in ROLES:
                return "a message's role is one of " + ", ".join(sorted(ROLES))
            if not (m.get("content") is None or text(m["content"])):
                return "a message's content is text"
            calls = m.get("tool_calls")
            if calls is not None and not (isinstance(calls, list) and all(function_call(c) for c in calls)):
                return "tool_calls are function calls"
    tools = data.get("tools")
    if tools is not None and not (isinstance(tools, list) and all(
            only(t, "type", "function") and t.get("type") == "function"
            and only(t.get("function"), "name", "description", "parameters", "strict")
            and isinstance(t["function"].get("name"), str) for t in tools)):
        return "tools are the app's own functions"
    choice = data.get("tool_choice")
    if not (choice in (None, "none", "auto", "required") if not isinstance(choice, dict) else
            only(choice, "type", "function") and choice.get("type") == "function" and only(choice.get("function"), "name")):
        return "tool_choice is none, auto, required or one of the app's functions"
    given = data.get("input")
    if given is not None and not (isinstance(given, str) or (isinstance(given, list) and given and all(
            isinstance(t, str) for t in given))):
        return "input is text"
    return None


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
            wrong = shape(data)
            if wrong:
                return await refuse(send, 400, "not allowed: " + wrong)
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


def merged(base, extra):
    """base with extra laid over it, key by key; extra's values win."""
    out = dict(base)
    for k, v in extra.items():
        out[k] = merged(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def settings_from_environment(argv, environ=os.environ, folder="/tmp"):
    """The platform's switches reach LiteLLM's config without a new image: GATEWAY_SETTINGS (JSON) is laid over the config
    named by --config, and a working copy is used instead. Without GATEWAY_SETTINGS nothing changes."""
    extra = environ.get("GATEWAY_SETTINGS")
    if not extra or "--config" not in argv:
        return argv
    import yaml

    i = argv.index("--config") + 1
    with open(argv[i]) as f:
        config = yaml.safe_load(f) or {}
    copy = os.path.join(folder, "gateway-config.yaml")
    with open(copy, "w") as f:
        yaml.safe_dump(merged(config, json.loads(extra)), f, sort_keys=False)
    # LiteLLM loads custom code (armor.FullArmor, usage.usage) from the config's folder: keep it findable
    import sys

    if os.path.dirname(argv[i]) not in sys.path:
        sys.path.insert(0, os.path.dirname(argv[i]))
    return argv[:i] + [copy] + argv[i + 1:]


def main():
    import sys

    sys.argv = settings_from_environment(sys.argv)
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
