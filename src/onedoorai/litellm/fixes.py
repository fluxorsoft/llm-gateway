"""Fixes for LiteLLM faults found while testing, applied when the gateway starts. Each is narrow, and each is checked again
whenever LiteLLM is upgraded (the upgrade lane runs the same tests).

1. Streamed Gemini answers (streamGenerateContent) lose their usage record, and their spend, at random: measured 3 of 5,
   6 of 10 and 7 of 8 recorded with real Gemini (LiteLLM 1.110.3). LiteLLM logs a stream only when it reads past its last
   event; a client that closes as soon as it has that event (curl, coding tools) gets the reading cancelled first, and
   nothing is logged. The fix logs what was collected when the stream is cancelled or dropped, once, and keeps the logging
   task until it is done (LiteLLM keeps no reference to it, which Python's documentation warns against).
"""
import asyncio

_kept = set()


def _report(task):
    import json
    import traceback

    if not task.cancelled() and task.exception() is not None:
        e = task.exception()
        print(json.dumps({"severity": "ERROR", "message": "litellm stream logging failed", "why": type(e).__name__,
                          "detail": str(e)[:300], "where": traceback.format_exception(e)[-3:]}), flush=True)


class _KeepTasks:
    """asyncio, except that create_task keeps each task until it finishes."""

    def __init__(self, real):
        self._real = real

    def create_task(self, coro, **kwargs):
        task = self._real.create_task(coro, **kwargs)
        _kept.add(task)
        task.add_done_callback(_kept.discard)
        task.add_done_callback(_report)
        return task

    def __getattr__(self, name):
        return getattr(self._real, name)


def _patch_stream(streaming_iterator):
    base = streaming_iterator.BaseGoogleGenAIGenerateContentStreamingIterator
    cls = streaming_iterator.AsyncGoogleGenAIGenerateContentStreamingIterator
    log, anext = base._handle_async_streaming_logging, cls.__anext__

    async def log_once(self):
        if getattr(self, "_onedoor_logged", False) or not self.collected_chunks:
            return
        self._onedoor_logged = True
        await log(self)

    async def anext_logged(self):
        try:
            return await anext(self)
        except (asyncio.CancelledError, GeneratorExit):
            await log_once(self)  # the client left after the last event: log what came
            raise

    def dropped(self):
        if getattr(self, "_onedoor_logged", False) or not getattr(self, "collected_chunks", None):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(log_once(self))
        _kept.add(task)
        task.add_done_callback(_kept.discard)

    base._handle_async_streaming_logging = log_once
    cls.__anext__ = anext_logged
    cls.__del__ = dropped


def apply():
    """Returns the names of the fixes applied."""
    done = []
    try:
        from litellm.google_genai import streaming_iterator
    except ImportError:
        return done
    if getattr(streaming_iterator, "asyncio", None) is asyncio:
        streaming_iterator.asyncio = _KeepTasks(asyncio)
        _patch_stream(streaming_iterator)
        done.append("google-genai-stream-logging")
    return done
