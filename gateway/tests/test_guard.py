"""The filter, without LiteLLM: python -m unittest discover -s gateway/tests"""
import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guard import Filter  # noqa: E402

OK = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}


async def app(scope, receive, send):
    body = (await receive()).get("body", b"") if scope["type"] == "http" else b""
    seen = json.dumps({"headers": [k.decode() for k, _ in scope["headers"]], "body": body.decode()}).encode()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": seen})


def call(body=None, path="/v1/chat/completions", method="POST", headers=(), query=b"", chunks=None):
    raw = body if isinstance(body, bytes) else json.dumps(OK if body is None else body).encode()
    parts = chunks or [raw]
    queue = [{"type": "http.request", "body": p, "more_body": i < len(parts) - 1} for i, p in enumerate(parts)]
    out = []

    async def receive():
        return queue.pop(0)

    async def send(m):
        out.append(m)

    scope = {"type": "http", "method": method, "path": path, "query_string": query, "headers": list(headers)}
    asyncio.run(Filter(app)(scope, receive, send))
    return out[0]["status"], json.loads(out[1]["body"])


class GuardTest(unittest.TestCase):
    def test_a_normal_call_passes(self):
        status, seen = call()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(seen["body"]), OK)

    def test_overrides_are_refused(self):
        for field in ("vertex_project", "vertex_location", "vertex_ai_project", "vertex_ai_location",
                      "vertex_credentials", "api_key", "api_base", "base_url", "extra_body", "extra_headers",
                      "litellm_params", "litellm_metadata", "metadata", "fallbacks", "mock_response", "num_retries",
                      "timeout", "guardrails", "cache", "no-log", "tags"):
            status, _ = call({**OK, field: "x"})
            self.assertEqual(status, 400, field)

    def test_only_the_apps_own_functions_are_tools(self):
        fn = {"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object"}}}
        self.assertEqual(call({**OK, "tools": [fn], "tool_choice": "auto"})[0], 200)
        self.assertEqual(call({**OK, "tools": [fn], "tool_choice": {"type": "function", "function": {"name": "f"}}})[0], 200)
        for tool in ({"google_search": {}}, {"googleSearch": {}}, {"type": "web_search"}, {"url_context": {}}, {"codeExecution": {}},
                     {"type": "computer_use"}, {"googleMaps": {}}, {"enterpriseWebSearch": {}}, {"type": "mcp", "server_url": "x"},
                     {**fn, "google_search": {}}, {"retrieval": {"vertex_rag_store": {}}}, {"type": "function"}, "x"):
            self.assertEqual(call({**OK, "tools": [tool]})[0], 400, tool)
        self.assertEqual(call({**OK, "tool_choice": {"type": "web_search"}})[0], 400)

    def test_messages_are_text_in_plain_shapes(self):
        good = [{"role": "system", "content": "s"}, {"role": "user", "content": [{"type": "text", "text": "u"}]},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "1", "content": "r"}]
        self.assertEqual(call({**OK, "messages": good})[0], 200)
        for m in ({"role": "user", "content": [{"type": "image_url", "image_url": {"url": "http://x"}}]},
                  {"role": "user", "content": [{"type": "file", "file": {"file_data": "x"}}]},
                  {"role": "user", "content": [{"type": "text", "text": "x", "cache_control": {}}]},
                  {"role": "user", "content": "x", "thinking_blocks": []}, {"role": "user", "content": "x", "images": []},
                  {"role": "model", "content": "x"}, {"role": "user", "content": {"text": "x"}}, "x",
                  {"role": "assistant", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": {}}}]}):
            self.assertEqual(call({**OK, "messages": [m]})[0], 400, m)
        for messages in ([], {}, "x"):
            self.assertEqual(call({**OK, "messages": messages})[0], 400, messages)

    def test_embeddings_input_is_text(self):
        e = lambda i: call({"model": "m", "input": i}, path="/v1/embeddings")[0]  # noqa: E731
        self.assertEqual((e("x"), e(["x", "y"])), (200, 200))
        self.assertEqual((e([1, 2]), e([[1]]), e([]), e({"a": 1})), (400, 400, 400, 400))

    def test_duplicate_and_escaped_names_are_refused(self):
        self.assertEqual(call(b'{"vertex_project":"a","model":"m","vertex_project":"b"}')[0], 400)
        self.assertEqual(call(b'{"vertex' + bytes([92]) + b'u005fproject":"a","model":"m"}')[0], 400)

    def test_other_bodies_are_refused(self):
        self.assertEqual(call(b"[1]")[0], 400)
        self.assertEqual(call(b"model=m&vertex_project=x")[0], 400)
        self.assertEqual(call(b"x" * 11_000_000)[0], 413)
        self.assertEqual(call(headers=[(b"content-encoding", b"gzip")])[0], 415)

    def test_only_the_inference_paths_exist(self):
        for path in ("/", "/ui", "/ui/", "/key/generate", "/key/list", "/model/info", "/v1/responses", "/v1/messages",
                     "/chat/completions", "/v1/chat/completions/", "/V1/chat/completions", "//v1/chat/completions",
                     "/vertex_ai/v1/projects/p", "/v1beta/models/m:generateContent", "/metrics", "/openapi.json"):
            for method in ("GET", "POST", "PUT", "DELETE"):
                self.assertEqual(call(path=path, method=method)[0], 404, (method, path))
        self.assertEqual(call(method="GET")[0], 404)

    def test_query_parameters_are_refused(self):
        self.assertEqual(call(query=b"vertex_project=x")[0], 400)

    def test_control_headers_are_dropped(self):
        _, seen = call(headers=[(b"authorization", b"Bearer k"), (b"x-litellm-api-key", b"k"),
                                (b"x-litellm-tags", b"t"), (b"x-litellm-timeout", b"1"), (b"api-key", b"k"),
                                (b"x-goog-api-key", b"k"), (b"content-length", b"9999")])
        self.assertEqual(sorted(seen["headers"]), ["authorization", "content-length", "content-type"])

    def test_a_body_in_pieces_is_read_whole(self):
        raw = json.dumps({**OK, "vertex_project": "x"}).encode()
        self.assertEqual(call(chunks=[raw[:20], raw[20:]])[0], 400)


if __name__ == "__main__":
    unittest.main()
