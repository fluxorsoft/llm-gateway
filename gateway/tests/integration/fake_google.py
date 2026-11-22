"""Stands in for Model Armor and the metadata server: records what it was shown, and answers by the words in it.

  BLOCKWORD  a match      SKIPWORD  a filter that did not run      PARTWORD  a partial run      FAILWORD  an error
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

seen = []


class H(BaseHTTPRequestHandler):
    def answer(self, code, value, kind="application/json"):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Metadata-Flavor", "Google")
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/seen":
            out, seen[:] = list(seen), []
            return self.answer(200, out)
        if p.endswith("/token"):
            return self.answer(200, {"access_token": "fake", "expires_in": 3000, "token_type": "Bearer"})
        if "/service-accounts/" in p:
            return self.answer(200, {"email": "fake@example.com", "aliases": ["default"], "scopes": ["x"]})
        self.answer(200, b"example-project" if p.endswith("project-id") else b"googleapis.com", "text/plain")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
        prompt = self.path.endswith(":sanitizeUserPrompt")
        text = body["userPromptData" if prompt else "modelResponseData"]["text"]
        seen.append({"prompt": prompt, "text": text})
        if "FAILWORD" in text:
            return self.answer(500, {"error": "no"})
        match = "MATCH_FOUND" if "BLOCKWORD" in text else "NO_MATCH_FOUND"
        ran = "EXECUTION_SKIPPED" if "SKIPWORD" in text else "EXECUTION_SUCCESS"
        self.answer(200, {"sanitizationResult": {
            "filterMatchState": match, "invocationResult": "PARTIAL" if "PARTWORD" in text else "SUCCESS",
            "filterResults": {
                "pi_and_jailbreak": {"piAndJailbreakFilterResult": {"executionState": ran, "matchState": match}},
                "sdp": {"sdpFilterResult": {"inspectResult": {"executionState": "EXECUTION_SUCCESS",
                                                              "matchState": "NO_MATCH_FOUND"}}}}}})

    def log_message(self, *a):
        pass


ThreadingHTTPServer((sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0", 9100), H).serve_forever()
