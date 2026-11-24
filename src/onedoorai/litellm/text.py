"""What the safety check reads, and when a check counts as complete. No LiteLLM here: the request filter uses it too."""
import os

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


def team_of(data):
    for k in ("metadata", "litellm_metadata"):
        m = (data or {}).get(k) or {}
        if m.get("user_api_key_team_alias") or m.get("user_api_key_team_id"):
            return m.get("user_api_key_team_alias") or m.get("user_api_key_team_id")
    return None
