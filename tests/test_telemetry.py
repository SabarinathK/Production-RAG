import pytest
from app.core.telemetry import (
    LLM_TOKENS, LLM_COST_USD, RETRIEVAL_SCORE, GROUNDEDNESS_SCORE
)
from rag.generator import ResponseGenerator

def test_telemetry_metrics_recording():
    # Test metric observation
    RETRIEVAL_SCORE.observe(0.85)
    GROUNDEDNESS_SCORE.observe(0.92)

    gen = ResponseGenerator()
    gen._record_llm_metrics("gpt-4o-mini", {"prompt_tokens": 100, "completion_tokens": 50})

    # Verify metric objects accept labels and observations
    tokens_val = LLM_TOKENS.labels(type="prompt", model="gpt-4o-mini")._value.get()
    assert tokens_val >= 100

    cost_val = LLM_COST_USD.labels(model="gpt-4o-mini")._value.get()
    assert cost_val > 0.0
