# onedoorai

One door to AI: the Python parts of [llm-gateway](https://github.com/fluxorsoft/llm-gateway), an open-source LLM gateway on
Google Cloud built on LiteLLM.

```bash
pip install onedoorai            # the command line
pip install "onedoorai[litellm]" # also the parts that run inside a LiteLLM proxy
```

| | |
|---|---|
| `onedoorai admin --url https://...` | A local door to the gateway's admin service: adds your Google identity, then open http://127.0.0.1:9000/ui/ |
| `onedoorai.litellm.armor.FullArmor` | A Model Armor guardrail that reads every message and every answer, or refuses the call |
| `onedoorai.litellm.guard` | The request filter that sees the raw request before LiteLLM does |
| `onedoorai.litellm.usage` | One usage record per call, never the words |

Apache 2.0. Not affiliated with BerriAI or Google.
