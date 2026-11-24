"""onedoorai: one door to AI. The Python parts of the LLM gateway (github.com/fluxorsoft/llm-gateway).

  onedoorai.litellm    the request filter, the full-check Model Armor guardrail and the usage record, for a LiteLLM proxy
                       (pip install "onedoorai[litellm]")
  onedoorai.admin      a local door to the gateway's admin service
  onedoorai.keys       makes and adopts team keys; LiteLLM chooses each value, which goes only to the team's secret
"""
__version__ = "1.1.0.dev0"
