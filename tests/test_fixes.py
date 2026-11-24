"""The streamed-Gemini logging fix, against a stand-in for LiteLLM's iterator: python -m unittest discover -s tests"""
import asyncio
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from onedoorai.litellm import fixes  # noqa: E402


def stand_in():
    logged = []

    class Base:
        def __init__(self):
            self.collected_chunks = []

        async def _handle_async_streaming_logging(self):
            logged.append(list(self.collected_chunks))

    class Stream(Base):
        def __init__(self, chunks, hang=False):
            super().__init__()
            self.chunks, self.hang = list(chunks), hang

        async def __anext__(self):
            if self.chunks:
                c = self.chunks.pop(0)
                self.collected_chunks.append(c)
                return c
            if self.hang:  # waiting for the end of the stream when the client leaves
                await asyncio.sleep(3600)
            await self._handle_async_streaming_logging()
            raise StopAsyncIteration

    module = types.SimpleNamespace(BaseGoogleGenAIGenerateContentStreamingIterator=Base,
                                   AsyncGoogleGenAIGenerateContentStreamingIterator=Stream, asyncio=asyncio)
    fixes._patch_stream(module)
    return Stream, logged


class FixesTest(unittest.TestCase):
    def test_a_finished_stream_is_logged_once(self):
        Stream, logged = stand_in()

        async def run():
            s = Stream([b"a", b"b"])
            got = []
            try:
                while True:
                    got.append(await s.__anext__())
            except StopAsyncIteration:
                pass
            return got
        self.assertEqual(asyncio.run(run()), [b"a", b"b"])
        self.assertEqual(logged, [[b"a", b"b"]])

    def test_a_stream_the_client_left_is_still_logged(self):
        Stream, logged = stand_in()

        async def run():
            s = Stream([b"a", b"b"], hang=True)
            await s.__anext__()
            await s.__anext__()
            t = asyncio.ensure_future(s.__anext__())
            await asyncio.sleep(0.01)
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0.01)
        asyncio.run(run())
        self.assertEqual(logged, [[b"a", b"b"]])


if __name__ == "__main__":
    unittest.main()
