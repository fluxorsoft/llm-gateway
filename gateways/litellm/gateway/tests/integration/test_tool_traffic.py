"""Tool traffic checked on the way, through the real gateway: GATEWAY=http://... python test_tool_traffic.py"""
import json
import os
import unittest
import urllib.request

GATEWAY = os.environ["GATEWAY"]


def call(name, arguments):
    body = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    r = urllib.request.Request(GATEWAY + "/mcp/", json.dumps(body).encode(), {"Authorization": "Bearer sk-test",
                               "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(r, timeout=60) as a:
        text = a.read().decode()
    for line in text.splitlines():
        if line.startswith("data: "):
            text = line[6:]
    return json.loads(text)


class ToolTrafficTest(unittest.TestCase):
    def test_a_clean_call_passes(self):
        got = call("tools-hello", {"who": "friend"})
        self.assertIn("hello friend", json.dumps(got.get("result")), got)

    def test_a_blocked_argument_never_reaches_the_tool(self):
        got = call("tools-hello", {"who": "BLOCKWORD"})
        self.assertIn("refused: the tool call", got.get("error", {}).get("message", ""), got)

    def test_a_blocked_answer_never_reaches_the_caller(self):
        got = call("tools-leak", {})
        self.assertIn("refused: the tool's answer", got.get("error", {}).get("message", ""), got)
        self.assertNotIn("BLOCKWORD", json.dumps(got))

    def test_a_call_not_fully_checked_is_refused(self):
        got = call("tools-hello", {"who": "SKIPWORD"})
        self.assertIn("not fully checked", got.get("error", {}).get("message", ""), got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
