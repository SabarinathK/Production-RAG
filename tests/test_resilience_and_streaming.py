import pytest
import asyncio
from app.core.retry import async_retry
from rag.generator import generator

@pytest.mark.asyncio
async def test_async_retry_success_after_retries():
    attempts = 0

    @async_retry(max_retries=3, initial_delay=0.01, backoff_factor=1.0)
    async def flaky_call():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("Transient network blip")
        return "success"

    res = await flaky_call()
    assert res == "success"
    assert attempts == 3

@pytest.mark.asyncio
async def test_async_retry_max_retries_exceeded():
    @async_retry(max_retries=2, initial_delay=0.01, backoff_factor=1.0)
    async def always_fails():
        raise ValueError("Persistent failure")

    with pytest.raises(ValueError, match="Persistent failure"):
        await always_fails()

@pytest.mark.asyncio
async def test_real_sse_streaming_generator():
    chunks = [{"payload": {"text": "Alpha document context", "filename": "doc.pdf", "page_number": 1}}]
    stream = generator.generate_response_stream("What is alpha?", chunks)

    events = []
    async for event in stream:
        events.append(event)

    assert len(events) > 0
    assert any("data:" in e for e in events)
    assert events[-1] == "data: [DONE]\n\n"
