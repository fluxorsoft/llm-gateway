"""Keeping the words, inside the gateway image: CAPTURE_TEAMS='["eng"]' ... python test_capture.py"""
import asyncio
import contextlib
import io
import json
import types
import unittest

from onedoorai.litellm import capture


def data(team, text="the question"):
    return {"litellm_call_id": "c1", "metadata": {"user_api_key_team_alias": team}, "messages": [{"role": "user", "content": text}]}


def lines(run):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = asyncio.run(run)
    return result, [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]


class CaptureTest(unittest.TestCase):
    def test_only_listed_teams_and_both_directions(self):
        c = capture.Capture()
        whole = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="the answer", tool_calls=None))])
        _, got = lines(c.async_pre_call_hook(None, None, data("eng"), "completion"))
        self.assertEqual([(l["team"], l["direction"], l["text"]) for l in got], [("eng", "question", "the question")])
        _, got = lines(c.async_post_call_success_hook(data("eng"), None, whole))
        self.assertEqual([(l["direction"], l["text"]) for l in got], [("answer", "the answer")])
        _, got = lines(c.async_pre_call_hook(None, None, data("orders"), "completion"))
        self.assertEqual(got, [])

    def test_streamed_answers_are_kept_whole_and_still_pass(self):
        c = capture.Capture()

        async def stream():
            for w in ("one ", "two"):
                yield types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=w))])

        async def run():
            return [ch async for ch in c.async_post_call_streaming_iterator_hook(None, stream(), data("eng"))]
        chunks, got = lines(run())
        self.assertEqual(len(chunks), 2)
        self.assertEqual([(l["direction"], l["text"]) for l in got], [("answer", "one two")])

    def test_long_text_is_cut_and_marked(self):
        l = capture.line("eng", "c", "question", "x" * (capture.LIMIT + 5))
        self.assertTrue(l["truncated"] and len(l["text"]) == capture.LIMIT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
