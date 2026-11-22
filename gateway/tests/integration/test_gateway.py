"""The gateway with the real LiteLLM: GATEWAY=http://... GOOGLE=http://... python test_gateway.py"""
import json
import os
import unittest
import urllib.error
import urllib.request

GATEWAY, GOOGLE = os.environ["GATEWAY"], os.environ["GOOGLE"]
BAD = "BLOCKWORD"
FN = {"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}}}
CALL = {"role": "assistant", "content": None,
        "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]}
BAD_CALL = {**CALL, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": BAD}}]}


def post(body, path="/v1/chat/completions"):
    r = urllib.request.Request(GATEWAY + path, json.dumps(body).encode(),
                               {"Authorization": "Bearer sk-test", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as a:
            return a.status, a.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def seen():
    return json.load(urllib.request.urlopen(GOOGLE + "/seen", timeout=10))


def chat(*messages, model="pretend", **more):
    return post({"model": model, "messages": list(messages), **more})


def say(role, content):
    return {"role": role, "content": content}


def tool(**f):
    return [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}, **f}}]


class GatewayTest(unittest.TestCase):
    def setUp(self):
        seen()

    def refused(self, r, word="Model Armor"):
        self.assertNotEqual(r[0], 200, r)
        self.assertIn(word, r[1])

    def test_a_clean_call_passes_and_is_read_whole(self):
        s, b = chat(say("system", "SYS"), say("user", "ONE"), say("assistant", "TWO"), say("user", "THREE"))
        self.assertEqual(s, 200, b)
        shown = seen()
        prompts = [x["text"] for x in shown if x["prompt"]]
        self.assertEqual(len(prompts), 1)
        for w in ("SYS", "ONE", "TWO", "THREE"):
            self.assertIn(w, prompts[0])
        self.assertEqual([x["text"] for x in shown if not x["prompt"]], ["a plain answer"])

    def test_the_answer_is_checked(self):
        for model in ("pretend-bad", "pretend-skip"):
            for stream in (False, True):
                with self.subTest(f"{model} stream={stream}"):
                    s, b = chat(say("user", "x"), model=model, stream=stream)
                    self.assertIn("Model Armor", b)
                    self.assertNotIn("an answer with", b)

    def test_bad_words_are_found_wherever_they_are(self):
        result = {"role": "tool", "tool_call_id": "1"}
        cases = {
            "last user": (say("user", BAD),),
            "system": (say("system", BAD), say("user", "x")),
            "developer": (say("developer", BAD), say("user", "x")),
            "earlier user": (say("user", BAD), say("assistant", "ok"), say("user", "x")),
            "assistant": (say("user", "x"), say("assistant", BAD), say("user", "y")),
            "ends with assistant": (say("user", BAD), say("assistant", "ok")),
            "ends with system": (say("user", BAD), say("system", "ok")),
            "ends with tool": (say("user", BAD), CALL, {**result, "content": "ok"}),
            "tool result": (say("user", "x"), CALL, {**result, "content": BAD}),
            "tool call arguments": (say("user", "x"), BAD_CALL, {**result, "content": "ok"}),
            "name": ({"role": "user", "name": BAD, "content": "x"},),
            "cut in two": (say("user", [{"type": "text", "text": "BLOCK"}, {"type": "text", "text": "WORD"}]),),
        }
        for name, messages in cases.items():
            with self.subTest(name):
                self.refused(chat(*messages))

    def test_bad_words_in_tools_and_formats(self):
        cases = {
            "description": {"tools": tool(description=BAD)},
            "property called type": {"tools": tool(parameters={"type": "object", "properties": {"type": {"description": BAD}}})},
            "property name": {"tools": tool(parameters={"type": "object", "properties": {BAD: {"type": "string"}}})},
            "format": {"response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": {"description": BAD}}}},
            "stop": {"stop": [BAD]},
        }
        for name, more in cases.items():
            with self.subTest(name):
                self.refused(chat(say("user", "x"), **more))

    def test_what_was_not_checked_whole_is_refused(self):
        for w in ("SKIPWORD", "PARTWORD", "FAILWORD"):
            with self.subTest(w):
                self.refused(chat(say("user", w)))
        self.refused(chat(say("user", "a" * 120_001)), "can be checked")
        self.refused(chat(say("system", "a" * 70_000), say("user", "b" * 70_000)), "can be checked")
        self.assertEqual(chat(say("user", "a" * 100_000))[0], 200)

    def test_embeddings_input_is_checked(self):
        self.refused(post({"model": "pretend-embed", "input": BAD}, "/v1/embeddings"))
        self.refused(post({"model": "pretend-embed", "input": ["x", BAD]}, "/v1/embeddings"))

    def test_provider_tools_and_pictures_are_refused(self):
        for t in ({"google_search": {}}, {"type": "web_search"}, {"url_context": {}}, {"code_execution": {}}):
            with self.subTest(str(t)):
                self.refused(chat(say("user", "x"), tools=[t]), "not allowed")
        self.refused(chat(say("user", [{"type": "image_url", "image_url": {"url": "http://x/y.png"}}])), "not allowed")
        s, b = chat(say("user", "x"), tools=[FN])
        self.assertEqual(s, 200, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
