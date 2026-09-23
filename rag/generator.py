import logging
import asyncio
import json
from typing import List, Dict, Any, AsyncGenerator
import httpx
from app.config import settings
from app.core.telemetry import LLM_TOKENS, LLM_COST_USD
from app.core.retry import async_retry

logger = logging.getLogger(__name__)

PRICING = {
    "gpt-4o-mini": {"prompt": 0.00000015, "completion": 0.0000006},
    "gpt-4o": {"prompt": 0.0000025, "completion": 0.00001},
    "llama-3.3-70b-versatile": {"prompt": 0.00000059, "completion": 0.00000079},
    "mixtral-8x7b-32768": {"prompt": 0.00000024, "completion": 0.00000024},
    "default": {"prompt": 0.0000005, "completion": 0.000001}
}

class ResponseGenerator:
    def _calculate_groundedness(self, answer: str, context_chunks: List[Dict[str, Any]]) -> float:
        if not context_chunks or not answer:
            return 0.0
        
        # Calculate term overlap between answer and retrieved passages
        context_words = set()
        for doc in context_chunks:
            text = doc.get("payload", {}).get("text", "").lower()
            context_words.update([w.strip(".,!?()") for w in text.split() if len(w) > 3])
        
        if not context_words:
            return 0.0
        
        answer_words = [w.strip(".,!?()") for w in answer.lower().split() if len(w) > 3]
        if not answer_words:
            return 0.0
        
        matches = sum(1 for w in answer_words if w in context_words)
        overlap_score = round(min(1.0, (matches / len(answer_words)) * 1.2), 2)
        return overlap_score

    @async_retry(max_retries=3, initial_delay=0.3, backoff_factor=2.0)
    async def _call_groq_llm(self, query: str, formatted_context: str) -> tuple:
        """Async Groq LLM call — returns (answer_text, usage_dict)."""
        try:
            prompt = (
                f"You are a helpful production RAG AI assistant.\n"
                f"System Context:\n{formatted_context}\n\n"
                f"User Question: {query}\n"
                f"Provide a clear, well-structured, and grounded plain text answer based strictly on the retrieved system context.\n"
                f"Format the answer cleanly using clear headings and readable bullet points with bold labels (e.g. • **Label:** Value). Do not use markdown pipe tables (|...|).\n"
                f"Preserve all legitimate business contact details, phone numbers, and email addresses present in the context without omitting or redacting them."
            )
            model_name = settings.GROQ_MODEL or "openai/gpt-oss-20b"
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2
                    }
                )
            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return content, usage
            else:
                logger.warning(f"Groq API call returned status {response.status_code}: {response.text}")
        except Exception as e:
            logger.warning(f"Groq LLM API call error: {e}")
        return None, {}

    @async_retry(max_retries=3, initial_delay=0.3, backoff_factor=2.0)
    async def _call_openai_llm(self, query: str, formatted_context: str) -> tuple:
        """Async OpenAI LLM call — returns (answer_text, usage_dict)."""
        try:
            prompt = f"System Context:\n{formatted_context}\n\nUser Question: {query}\nProvide a concise grounded answer with citations."
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2
                    }
                )
            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return content, usage
        except Exception as e:
            logger.warning(f"LLM API call failed, falling back to local synthesis generator: {e}")
        return None, {}

    def _record_llm_metrics(self, model_name: str, usage: Dict[str, Any]):
        if not usage:
            return
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        LLM_TOKENS.labels(type="prompt", model=model_name).inc(prompt_tokens)
        LLM_TOKENS.labels(type="completion", model=model_name).inc(completion_tokens)

        # Lookup pricing per token
        p_table = PRICING.get(model_name, PRICING.get(settings.GROQ_MODEL, PRICING["default"]))
        cost = (prompt_tokens * p_table.get("prompt", 0.0)) + (completion_tokens * p_table.get("completion", 0.0))
        LLM_COST_USD.labels(model=model_name).inc(cost)

    async def generate_response(self, query: str, context_chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generates a grounded response based on top context chunks with citations and groundedness score.
        All LLM calls are fully async and do not block the event loop.
        """
        citations = []
        context_passages = []

        for idx, doc in enumerate(context_chunks, 1):
            payload = doc.get("payload", {})
            text = payload.get("text", "")
            filename = payload.get("filename", "document.pdf")
            page = payload.get("page_number", 1)

            citation_id = f"[{idx}]"
            citations.append({
                "citation_id": citation_id,
                "document_name": filename,
                "page_number": page,
                "snippet": text[:150] + "..." if len(text) > 150 else text
            })
            context_passages.append(f"{citation_id} (Source: {filename}, Page {page})\n{text}")

        formatted_context = "\n\n".join(context_passages)
        answer_text = None
        is_llm = False
        model_used = "synthesis_fallback"

        if settings.GROQ_API_KEY:
            answer_text, usage = await self._call_groq_llm(query, formatted_context)
            if answer_text:
                is_llm = True
                model_used = f"groq:{settings.GROQ_MODEL}"
                self._record_llm_metrics(model_used, usage)
        
        if not answer_text and settings.OPENAI_API_KEY:
            answer_text, usage = await self._call_openai_llm(query, formatted_context)
            if answer_text:
                is_llm = True
                model_used = "gpt-4o-mini"
                self._record_llm_metrics(model_used, usage)

        if not answer_text:
            # Deterministic synthesis generator when external LLM API key is not configured
            if context_chunks:
                bullet_points = []
                for idx, doc in enumerate(context_chunks, 1):
                    t = doc.get("payload", {}).get("text", "")
                    bullet_points.append(f"{idx}. {t} [{idx}]")
                answer_text = (
                    f"Based on retrieved system documentation for '{query}':\n\n"
                    + "\n".join(bullet_points)
                )
            else:
                answer_text = f"No relevant document context found to answer query: '{query}'."

        groundedness = self._calculate_groundedness(answer_text, context_chunks)

        return {
            "answer": answer_text,
            "citations": citations,
            "groundedness_score": groundedness,
            "is_llm_generated": is_llm,
            "model_used": model_used
        }

    async def generate_response_stream(self, query: str, context_chunks: List[Dict[str, Any]]) -> AsyncGenerator[str, None]:
        """
        Yields real token-by-token SSE streaming events directly from the LLM provider API when available,
        or falls back gracefully to word-by-word deterministic streaming.
        """
        context_passages = []
        for idx, doc in enumerate(context_chunks, 1):
            payload = doc.get("payload", {})
            text = payload.get("text", "")
            filename = payload.get("filename", "document.pdf")
            page = payload.get("page_number", 1)
            context_passages.append(f"[{idx}] (Source: {filename}, Page {page})\n{text}")

        formatted_context = "\n\n".join(context_passages)

        # 1. Real SSE Streaming from Groq API if key is available
        if settings.GROQ_API_KEY:
            try:
                prompt = (
                    f"You are a helpful production RAG AI assistant.\n"
                    f"System Context:\n{formatted_context}\n\n"
                    f"User Question: {query}\n"
                    f"Provide a clear, well-structured, and grounded plain text answer based strictly on the retrieved system context.\n"
                    f"Format the answer cleanly using clear headings and readable bullet points with bold labels (e.g. • **Label:** Value). Do not use markdown pipe tables (|...|).\n"
                    f"Preserve all legitimate business contact details, phone numbers, and email addresses present in the context without omitting or redacting them."
                )
                model_name = settings.GROQ_MODEL or "openai/gpt-oss-20b"
                async with httpx.AsyncClient(timeout=30.0) as client:
                    async with client.stream(
                        "POST",
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": model_name,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.2,
                            "stream": True
                        }
                    ) as response:
                        if response.status_code == 200:
                            async for line in response.aiter_lines():
                                if line.startswith("data: "):
                                    data_str = line[6:].strip()
                                    if data_str == "[DONE]":
                                        break
                                    try:
                                        chunk_json = json.loads(data_str)
                                        delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                                        token = delta.get("content", "")
                                        if token:
                                            lines = token.split("\n")
                                            if len(lines) > 1:
                                                yield "".join(f"data: {l}\n" for l in lines) + "\n"
                                            else:
                                                yield f"data: {token}\n\n"
                                    except Exception:
                                        pass
                            yield "data: [DONE]\n\n"
                            return
            except Exception as e:
                logger.warning(f"Groq SSE stream failed, attempting fallback: {e}")

        # 2. Real SSE Streaming from OpenAI API if key is available
        if settings.OPENAI_API_KEY:
            try:
                prompt = f"System Context:\n{formatted_context}\n\nUser Question: {query}\nProvide a concise grounded answer with citations."
                async with httpx.AsyncClient(timeout=30.0) as client:
                    async with client.stream(
                        "POST",
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.2,
                            "stream": True
                        }
                    ) as response:
                        if response.status_code == 200:
                            async for line in response.aiter_lines():
                                if line.startswith("data: "):
                                    data_str = line[6:].strip()
                                    if data_str == "[DONE]":
                                        break
                                    try:
                                        chunk_json = json.loads(data_str)
                                        delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                                        token = delta.get("content", "")
                                        if token:
                                            yield f"data: {token}\n\n"
                                    except Exception:
                                        pass
                            yield "data: [DONE]\n\n"
                            return
            except Exception as e:
                logger.warning(f"OpenAI SSE stream failed: {e}")

        # 3. Fallback to full response generator stream
        res = await self.generate_response(query, context_chunks)
        full_answer = res["answer"]
        words = full_answer.split(" ")

        for i, word in enumerate(words):
            chunk = word + (" " if i < len(words) - 1 else "")
            lines = chunk.split("\n")
            if len(lines) > 1:
                yield "".join(f"data: {l}\n" for l in lines) + "\n"
            else:
                yield f"data: {chunk}\n\n"
            await asyncio.sleep(0.02)
        yield "data: [DONE]\n\n"

    async def generate_cached_stream(self, cached_answer: str) -> AsyncGenerator[str, None]:
        words = cached_answer.split(" ")
        for i, word in enumerate(words):
            chunk = word + (" " if i < len(words) - 1 else "")
            lines = chunk.split("\n")
            if len(lines) > 1:
                yield "".join(f"data: {l}\n" for l in lines) + "\n"
            else:
                yield f"data: {chunk}\n\n"
            await asyncio.sleep(0.01)
        yield "data: [DONE]\n\n"

generator = ResponseGenerator()
