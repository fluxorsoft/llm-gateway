"""Keeping the words, by choice: for the teams in CAPTURE_TEAMS only, one log line per question and per answer.

Lines are {"message": "llm words", "team", "call_id", "direction": "question" | "answer", "text", "truncated"}. The
platform routes them to a BigQuery dataset in the team's own project, deleted after the team's `capture.days`, readable
only by its named readers, and keeps them out of the gateway project's own logs. Without CAPTURE_TEAMS this does nothing.
"""
import json
import os

from litellm.integrations.custom_logger import CustomLogger

from .armor import everything, team_of

TEAMS = set(json.loads(os.environ.get("CAPTURE_TEAMS") or "[]"))
LIMIT = 100_000  # characters per line: Cloud Logging takes up to 256 KB an entry


def line(team, call_id, direction, text):
    return {"message": "llm words", "severity": "INFO", "team": team, "call_id": call_id, "direction": direction,
            "text": text[:LIMIT], "truncated": len(text) > LIMIT}


def answer_text(response):
    """The words of a whole answer: chat choices, or the Responses API's output."""
    parts = []
    for choice in getattr(response, "choices", None) or []:
        message = getattr(choice, "message", None)
        parts += [getattr(message, "content", None) or "", getattr(message, "reasoning_content", None) or ""]
        for call in getattr(message, "tool_calls", None) or []:
            parts.append(getattr(getattr(call, "function", None), "arguments", None) or "")
    for item in getattr(response, "output", None) or []:
        for c in getattr(item, "content", None) or []:
            parts.append(getattr(c, "text", None) or "")
    return "\n".join(p for p in parts if p)


class Capture(CustomLogger):
    def write(self, *args):
        try:
            print(json.dumps(line(*args)), flush=True)
        except Exception:  # keeping words must never fail a call
            pass

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        team = team_of(data)
        if team in TEAMS:
            self.write(team, data.get("litellm_call_id"), "question", everything(data))
        return data

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        team = team_of(data)
        if team in TEAMS:
            self.write(team, data.get("litellm_call_id"), "answer", answer_text(response))
        return response

    async def async_post_call_streaming_iterator_hook(self, user_api_key_dict, response, request_data):
        team, words = team_of(request_data), []
        async for chunk in response:
            if team in TEAMS:
                for choice in getattr(chunk, "choices", None) or []:
                    words.append(getattr(getattr(choice, "delta", None), "content", None) or "")
            yield chunk
        if team in TEAMS:
            self.write(team, request_data.get("litellm_call_id"), "answer", "".join(words))


capture = Capture()
