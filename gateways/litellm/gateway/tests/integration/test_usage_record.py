"""The usage record, inside the gateway image (LiteLLM installed):
docker run --rm -e PYTHONPATH=/w/src -v $PWD:/w --entrypoint /app/.venv/bin/python gateway:test \
  /w/gateways/litellm/gateway/tests/integration/test_usage_record.py"""
import unittest

from onedoorai.litellm import usage


class RecordTest(unittest.TestCase):
    def test_a_tool_call_names_the_tool_and_its_server(self):
        p = {"id": "c1", "model": "MCP: files-read", "metadata": {"user_api_key_team_alias": "orders", "user_api_key_alias": "orders-5",
             "mcp_tool_call_metadata": {"name": "read", "mcp_server_name": "files", "arguments": {"path": "x"}}}}
        r = usage.record({"standard_logging_object": p}, True)
        self.assertEqual((r["tool"], r["tool_server"], r["team"]), ("read", "files", "orders"))
        self.assertNotIn("path", str(r))  # never the arguments

    def test_a_model_call_has_no_tool(self):
        r = usage.record({"standard_logging_object": {"id": "c2", "metadata": {}}}, True)
        self.assertEqual((r["tool"], r["tool_server"]), (None, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
