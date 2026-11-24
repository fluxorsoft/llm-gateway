"""The developers' door beside the apps' door, with the real LiteLLM:
GATEWAY=http://... DEVELOPER=http://... GOOGLE=http://... python test_developer_door.py"""
import json
import os
import unittest
import urllib.error
import urllib.request

GATEWAY, DEVELOPER, GOOGLE = os.environ["GATEWAY"], os.environ["DEVELOPER"], os.environ["GOOGLE"]
BAD = "BLOCKWORD"


def post(base, path, body, header="Authorization"):
    h = {"Content-Type": "application/json", header: "Bearer sk-test" if header == "Authorization" else "sk-test"}
    r = urllib.request.Request(base + path, json.dumps(body).encode(), h)
    try:
        with urllib.request.urlopen(r, timeout=60) as a:
            return a.status, a.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def seen():
    return json.load(urllib.request.urlopen(GOOGLE + "/seen", timeout=10))


def message(text):
    return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}]


def gemini(*parts, system=None):
    body = {"contents": [{"role": "user", "parts": list(parts)}]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    return post(DEVELOPER, "/v1beta/models/pretend:generateContent", body, header="x-goog-api-key")


class DeveloperDoorTest(unittest.TestCase):
    def setUp(self):
        seen()

    def test_responses_api_is_checked(self):
        self.assertEqual(post(DEVELOPER, "/v1/responses", {"model": "pretend", "input": message("hello")})[0], 200)
        for body in ({"model": "pretend", "input": message(BAD)}, {"model": "pretend", "instructions": BAD, "input": message("hi")}):
            s, b = post(DEVELOPER, "/v1/responses", body)
            self.assertEqual(s, 400, b)
            self.assertIn("blocked", b.lower())

    def test_gemini_api_is_checked(self):
        self.assertEqual(gemini({"text": "hello"})[0], 200)
        for s, b in (gemini({"text": BAD}), gemini({"text": "hi"}, system=BAD)):
            self.assertEqual(s, 400, b)
            self.assertIn("blocked", b.lower())

    def test_opaque_signatures_are_not_read(self):
        body = {"contents": [{"role": "model", "parts": [{"text": "ok", "thoughtSignature": "A" * 200_000}]},
                             {"role": "user", "parts": [{"text": "hi"}]}]}
        self.assertEqual(post(DEVELOPER, "/v1beta/models/pretend:generateContent", body, header="x-goog-api-key")[0], 200)

    def test_provider_tools_files_and_other_queries_are_refused(self):
        cases = [
            post(DEVELOPER, "/v1/responses", {"model": "pretend", "input": message("hi"), "tools": [{"type": "web_search"}]}),
            post(DEVELOPER, "/v1/responses", {"model": "pretend", "input": [{"type": "message", "role": "user", "content": [{"type": "input_image", "image_url": "x"}]}]}),
            gemini({"inlineData": {"mimeType": "image/png", "data": "AA"}}),
            post(DEVELOPER, "/v1beta/models/pretend:generateContent?key=x", {"contents": []}, header="x-goog-api-key"),
            post(DEVELOPER, "/v1/responses", {"model": "pretend", "input": "hi", "vertex_project": "other"}),
        ]
        for s, b in cases:
            self.assertEqual(s, 400, b)

    def test_answers_are_not_held_on_this_door_and_are_on_the_other(self):
        body = {"model": "pretend-bad", "messages": [{"role": "user", "content": "x"}]}
        self.assertEqual(post(DEVELOPER, "/v1/chat/completions", body)[0], 200)
        s, b = post(GATEWAY, "/v1/chat/completions", body)
        self.assertEqual(s, 400, b)

    def test_a_key_can_read_only_its_own_spend(self):
        # LiteLLM answers only about the key that asks (proven with a database in the spikes); here: the door lets that one
        # read through, never with a query (so never about another key), and the apps' door does not have it
        def get(base, path):
            try:
                with urllib.request.urlopen(urllib.request.Request(base + path, headers={"Authorization": "Bearer sk-test"}), timeout=30) as a:
                    return a.status, a.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode()
        s, b = get(DEVELOPER, "/key/info")
        self.assertNotIn("gateway_refused", b)  # passed to LiteLLM
        s, b = get(DEVELOPER, "/key/info?key=sk-other")
        self.assertEqual((s, "gateway_refused" in b), (400, True))
        self.assertEqual(get(GATEWAY, "/key/info")[0], 404)

    def test_the_apps_door_has_no_developer_paths(self):
        self.assertEqual(post(GATEWAY, "/v1/responses", {"model": "pretend", "input": "hi"})[0], 404)
        self.assertEqual(post(GATEWAY, "/v1beta/models/pretend:generateContent", {"contents": []}, header="x-goog-api-key")[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
