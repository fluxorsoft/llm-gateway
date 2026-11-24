"""Model Armor on everything a caller sends and everything a model answers.

LiteLLM's own Model Armor guardrail (1.108.15) shows Model Armor only the last block of user messages, takes a check that
was skipped for a pass, and does not read embeddings input or a model's reasoning. This one:

- reads every message (system, user, assistant, tool), embeddings input, tools, the response format and stop words;
- refuses a request too long to be checked whole;
- refuses when Model Armor did not run, or skipped, any filter (no full check, no call), for questions and for answers;
- reads a model's reasoning with its answer.

The rest (the call to Model Armor, streamed answers held until checked, failing closed) is LiteLLM's.
"""
import contextvars
import json
import os

from fastapi import HTTPException
from litellm.proxy.guardrails.guardrail_hooks.model_armor.model_armor import ModelArmorAPIError, ModelArmorGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from .text import FIELDS, FORMAT, MAX_TEXT, OPAQUE, WITH_NAMES, everything, incomplete, team_of, words  # noqa: F401

# Safety rules per team: {team: template}; a team not named uses the template in the settings. The template is chosen for
# each request from the caller's team and held in a per-request value, so concurrent calls never mix.
TEMPLATES = json.loads(os.environ.get("ARMOR_TEMPLATES") or "{}")
PROVIDER_TOOLS = json.loads(os.environ.get("PROVIDER_TOOLS") or "{}")  # {team: [google_search, ...]}: allowed by request
_template = contextvars.ContextVar("armor_template", default=None)




NOTHING_TO_CHECK = {"sanitizationResult": {"filterMatchState": "NO_MATCH_FOUND", "invocationResult": "SUCCESS",
                                         "filterResults": {"blank": {"executionState": "EXECUTION_SUCCESS"}}}}


class FullArmor(ModelArmorGuardrail):
    @property
    def template_id(self):
        return _template.get() or self._default_template

    @template_id.setter
    def template_id(self, value):
        self._default_template = value

    async def make_model_armor_request(self, *args, request_data=None, **kwargs):
        content = kwargs.get("content", args[0] if args else None)
        source = kwargs.get("source", args[1] if len(args) > 1 else "user_prompt")
        if source == "model_response" and not (content or "").strip() and not kwargs.get("file_bytes"):
            # a blank answer (the model spent its allowance thinking): Model Armor refuses empty text with a 400, which
            # LiteLLM turns into a 500 for the caller. There are no words to check, so it passes as checked.
            return NOTHING_TO_CHECK
        token = _template.set(TEMPLATES.get(team_of(request_data)))
        try:
            return await super().make_model_armor_request(*args, request_data=request_data, **kwargs)
        finally:
            _template.reset(token)

    # LiteLLM runs only the hooks it finds in the class itself, not inherited ones: without these lines a streamed answer
    # would go out unchecked. gateways/litellm/gateway/tests/integration proves it does not.
    async_moderation_hook = ModelArmorGuardrail.async_moderation_hook
    async_post_call_success_hook = ModelArmorGuardrail.async_post_call_success_hook
    async_post_call_streaming_iterator_hook = ModelArmorGuardrail.async_post_call_streaming_iterator_hook
    apply_guardrail = ModelArmorGuardrail.apply_guardrail

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        missing = [n for n, v in vars(ModelArmorGuardrail).items()
                   if n.startswith("async_") and callable(v) and n not in vars(FullArmor)]
        if missing or not self.use_native_lifecycle_hooks:  # a new LiteLLM: look at it before it runs
            raise RuntimeError("LiteLLM's Model Armor guardrail has changed; armor.py must be reviewed: " + ", ".join(missing))

    def _should_block_content(self, armor_response, allow_sanitization=False):
        return bool(incomplete(armor_response)) or super()._should_block_content(armor_response, allow_sanitization)

    def _extract_content_from_response(self, response):
        text = [super()._extract_content_from_response(response) or ""]
        for choice in getattr(response, "choices", None) or []:
            message = getattr(choice, "message", None)
            text.append(getattr(message, "reasoning_content", None) or "")
            for block in getattr(message, "thinking_blocks", None) or []:
                text.append((block.get("thinking") if isinstance(block, dict) else getattr(block, "thinking", "")) or "")
        return "\n".join(t for t in text if t)

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if self.should_run_guardrail(data=data, event_type=GuardrailEventHooks.pre_call) is not True:
            return data
        if PROVIDER_TOOLS:  # the filter let provider tools through for some team: only that team may use them
            from .guard import provider_tool

            asked = {provider_tool(t) for t in data.get("tools") or []} - {None}
            refused = sorted(asked - set(PROVIDER_TOOLS.get(team_of(data)) or []))
            if refused:
                raise HTTPException(400, {"error": "not allowed: the provider's " + ", ".join(refused) + " is not allowed for this team"})
        text = everything(data)
        if not text:
            raise HTTPException(400, {"error": "there is no text to check"})
        if len(text) > MAX_TEXT:
            raise HTTPException(400, {"error": f"the request has {len(text)} characters; at most {MAX_TEXT} can be checked"})
        try:
            answer = await self.make_model_armor_request(content=text, source="user_prompt", request_data=data)
        except ModelArmorAPIError:
            raise
        except Exception as e:  # no check, no call
            raise ModelArmorAPIError(f"Model Armor API error ({type(e).__name__})") from None
        if self._should_block_content(answer):
            why = incomplete(answer)
            raise HTTPException(400, {"error": "Content blocked by Model Armor" + (f" ({why})" if why else "")})
        return data
