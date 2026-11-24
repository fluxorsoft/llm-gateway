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

# Model Armor checks 65,536 tokens for tricks, about 4 characters each in English. This leaves room for other languages;
# text that is still too long comes back as a skipped filter, and is refused too.
MAX_TEXT = int(os.environ.get("ARMOR_MAX_CHARACTERS", "120000"))
WITH_NAMES = ("tools", "tool_choice", "response_format")  # property names are the caller's words too
FIELDS = ("messages", "input", "instructions", "contents", "systemInstruction", "stop", *WITH_NAMES)
OPAQUE = {"thoughtSignature", "encrypted_content"}  # not text: signatures and sealed reasoning that the tool sends back


# the fixed words of the format, which would only be noise in a check; anything else in these fields is read
FORMAT = {"role": {"system", "developer", "user", "assistant", "tool", "model"},
          "type": {"text", "function", "message", "function_call", "function_call_output", "input_text", "output_text",
                   "reasoning", "namespace"}}


def words(value, names=False):
    """Every string in a JSON value, in order; with the names of its fields when asked."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if names:
                yield str(k)
            if k in OPAQUE:
                continue
            if not (isinstance(v, str) and v in FORMAT.get(k, ())):
                yield from words(v, names)
    elif isinstance(value, (list, tuple)) and value and all(isinstance(v, dict) and v.get("type") == "text" for v in value):
        yield "".join(t for v in value for t in words(v, names))  # parts of one text: a word cut in two is read whole
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from words(v, names)


def everything(data):
    return "\n".join(t for f in FIELDS for t in words(data.get(f), f in WITH_NAMES) if t.strip())


def incomplete(answer):
    """Why this check does not count as a full one, or None. A field that is not there is not a fault."""
    result = answer.get("sanitizationResult") or {}
    if result.get("invocationResult") not in (None, "SUCCESS"):
        return "invocation " + str(result["invocationResult"])
    if not result.get("filterResults"):
        return "no filter results"
    stack = [result["filterResults"]]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            if v.get("executionState") not in (None, "EXECUTION_SUCCESS"):
                return "a filter was not run"
            stack.extend(v.values())
        elif isinstance(v, (list, tuple)):
            stack.extend(v)
    return None


# Safety rules per team: {team: template}; a team not named uses the template in the settings. The template is chosen for
# each request from the caller's team and held in a per-request value, so concurrent calls never mix.
TEMPLATES = json.loads(os.environ.get("ARMOR_TEMPLATES") or "{}")
PROVIDER_TOOLS = json.loads(os.environ.get("PROVIDER_TOOLS") or "{}")  # {team: [google_search, ...]}: allowed by request
_template = contextvars.ContextVar("armor_template", default=None)


def team_of(data):
    for k in ("metadata", "litellm_metadata"):
        m = (data or {}).get(k) or {}
        if m.get("user_api_key_team_alias") or m.get("user_api_key_team_id"):
            return m.get("user_api_key_team_alias") or m.get("user_api_key_team_id")
    return None


class FullArmor(ModelArmorGuardrail):
    @property
    def template_id(self):
        return _template.get() or self._default_template

    @template_id.setter
    def template_id(self, value):
        self._default_template = value

    async def make_model_armor_request(self, *args, request_data=None, **kwargs):
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
