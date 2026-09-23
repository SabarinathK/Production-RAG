import logging
from typing import List, Dict, Any, Optional
import httpx
from app.config import settings
from app.core.database import get_qdrant_client
from app.core.retry import async_retry

logger = logging.getLogger(__name__)

# Allowed MIME types for document uploads
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",  # .doc
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "text/csv",
}


class EmbeddingGenerator:
    """
    Generates text embeddings via Google Gemini Embedding API using GOOGLE_API_KEY from environment configuration.
    All HTTP calls are async via httpx.AsyncClient to avoid blocking the event loop.
    """
    @async_retry(max_retries=3, initial_delay=0.3, backoff_factor=2.0)
    async def embed_query(self, text: str) -> List[float]:
        api_key = settings.GOOGLE_API_KEY
        if not api_key:
            logger.error("GOOGLE_API_KEY is missing from environment configuration.")
            raise RuntimeError("GOOGLE_API_KEY is required in .env for Gemini embeddings.")

        target_model = settings.GEMINI_EMBEDDING_MODEL or "gemini-embedding-2"
        formatted_model = target_model if target_model.startswith("models/") else f"models/{target_model}"

        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/{formatted_model}:embedContent?key={api_key}"
            payload = {
                "model": formatted_model,
                "content": {
                    "parts": [{"text": text}]
                }
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, json=payload)

            if res.status_code == 200:
                data = res.json()
                embedding = data.get("embedding", {}).get("values", [])
                if embedding:
                    return embedding

            # Fallback to gemini-embedding-001 if gemini-embedding-2 is unavailable
            if res.status_code == 404 and "gemini-embedding-2" in formatted_model:
                fallback_model = "models/gemini-embedding-001"
                logger.warning(f"Embedding model {formatted_model} returned 404. Trying fallback {fallback_model}...")
                fallback_url = f"https://generativelanguage.googleapis.com/v1beta/{fallback_model}:embedContent?key={api_key}"
                fallback_payload = {
                    "model": fallback_model,
                    "content": {
                        "parts": [{"text": text}]
                    }
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    res_fb = await client.post(fallback_url, json=fallback_payload)

                if res_fb.status_code == 200:
                    fb_data = res_fb.json()
                    fb_embedding = fb_data.get("embedding", {}).get("values", [])
                    if fb_embedding:
                        return fb_embedding

            logger.error(f"Google Gemini Embedding API error (HTTP {res.status_code}): {res.text}")
            raise RuntimeError(f"Google Gemini Embedding API returned status {res.status_code}: {res.text}")

        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"Gemini Embedding API execution failure: {e}")
            raise RuntimeError(f"Gemini Embedding API call failed: {e}")

embedding_generator = EmbeddingGenerator()


class HybridRetriever:
    def reciprocal_rank_fusion(self, dense_results: List[Dict], sparse_results: List[Dict], k: int = 60) -> List[Dict]:
        """
        Combines dense vector and sparse BM25 search rankings using Reciprocal Rank Fusion.
        """
        scores: Dict[str, float] = {}
        doc_map: Dict[str, Dict] = {}

        for rank, doc in enumerate(dense_results):
            doc_id = doc.get("id") or str(doc.get("payload", {}).get("chunk_id"))
            scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (k + rank + 1))
            doc_map[doc_id] = doc

        for rank, doc in enumerate(sparse_results):
            doc_id = doc.get("id") or str(doc.get("payload", {}).get("chunk_id"))
            scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (k + rank + 1))
            if doc_id not in doc_map:
                doc_map[doc_id] = doc

        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        fused_docs = []
        for doc_id in sorted_ids:
            item = doc_map[doc_id]
            item["rrf_score"] = round(scores[doc_id], 4)
            fused_docs.append(item)

        return fused_docs

    async def _fetch_full_corpus_chunks(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieves document chunks across the full collection (or filtered by user_id)
        to build the full-corpus BM25 index independently of vector search.
        """
        corpus_chunks = []
        try:
            client = get_qdrant_client()
            query_filter = None
            if user_id:
                try:
                    from qdrant_client.http.models import Filter, FieldCondition, MatchValue
                    query_filter = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])
                except Exception:
                    pass

            if hasattr(client, "scroll"):
                try:
                    res, _ = client.scroll(
                        collection_name=settings.QDRANT_COLLECTION,
                        scroll_filter=query_filter,
                        limit=1000,
                        with_payload=True,
                        with_vectors=False
                    )
                    for hit in res:
                        corpus_chunks.append({
                            "id": str(getattr(hit, "id", "")),
                            "payload": getattr(hit, "payload", {}) or {}
                        })
                    return corpus_chunks
                except Exception as sdk_err:
                    logger.warning(f"Qdrant SDK scroll note ({sdk_err}), trying HTTP scroll fallback...")

            if settings.QDRANT_HOST != ":memory:":
                url = f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}/collections/{settings.QDRANT_COLLECTION}/points/scroll"
                payload = {
                    "limit": 1000,
                    "with_payload": True,
                    "with_vector": False
                }
                if user_id:
                    payload["filter"] = {
                        "must": [{"key": "user_id", "match": {"value": user_id}}]
                    }
                async with httpx.AsyncClient(timeout=5.0) as client_http:
                    res = await client_http.post(url, json=payload)
                if res.status_code == 200:
                    raw_points = res.json().get("result", {}).get("points", [])
                    for p in raw_points:
                        corpus_chunks.append({
                            "id": str(p.get("id", "")),
                            "payload": p.get("payload", {})
                        })
        except Exception as e:
            logger.warning(f"Full-corpus scroll query note: {e}")

        return corpus_chunks

    async def search(self, queries: List[str], top_k: int = 5, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Async hybrid search — dense vector and full-corpus BM25 search fused via RRF."""
        if not queries:
            return []

        primary_query = queries[0]
        query_vector = await embedding_generator.embed_query(primary_query)

        # 1. Qdrant Dense Vector Search
        dense_results = []
        try:
            client = get_qdrant_client()
            hits = None

            query_filter = None
            if user_id:
                try:
                    from qdrant_client.http.models import Filter, FieldCondition, MatchValue
                    query_filter = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])
                except Exception as filter_err:
                    logger.warning(f"Could not construct Qdrant Filter object ({filter_err})")

            if hasattr(client, "query_points"):
                try:
                    kwargs = {
                        "collection_name": settings.QDRANT_COLLECTION,
                        "query": query_vector,
                        "limit": top_k * 2,
                        "with_payload": True,
                    }
                    if query_filter:
                        kwargs["query_filter"] = query_filter
                    res = client.query_points(**kwargs)
                    hits = res.points if hasattr(res, "points") else res
                except Exception as sdk_err:
                    logger.warning(f"Qdrant SDK query_points note ({sdk_err}), using HTTP search API fallback...")
                    hits = None

            if hits is None:
                if settings.QDRANT_HOST == ":memory:":
                    hits = []
                else:
                    url = f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}/collections/{settings.QDRANT_COLLECTION}/points/search"
                    payload = {
                        "vector": query_vector,
                        "limit": top_k * 2,
                        "with_payload": True
                    }
                    if user_id:
                        payload["filter"] = {
                            "must": [{"key": "user_id", "match": {"value": user_id}}]
                        }
                    async with httpx.AsyncClient(timeout=5.0) as client_http:
                        res = await client_http.post(url, json=payload)

                    if res.status_code == 200:
                        raw_hits = res.json().get("result", [])
                        hits = []
                        for h in raw_hits:
                            class HitObj:
                                pass
                            obj = HitObj()
                            obj.id = h.get("id")
                            obj.score = h.get("score", 0.0)
                            obj.payload = h.get("payload", {})
                            hits.append(obj)
                    else:
                        raise RuntimeError(f"Qdrant vector search HTTP query failed ({res.status_code}): {res.text}")

            for hit in hits:
                hit_id = str(getattr(hit, "id", ""))
                hit_score = float(getattr(hit, "score", 0.0))
                hit_payload = getattr(hit, "payload", {}) or {}
                dense_results.append({
                    "id": hit_id,
                    "score": hit_score,
                    "payload": hit_payload
                })
        except Exception as e:
            logger.error(f"Qdrant vector search query error: {e}")
            raise

        # 2. BM25 Sparse Search over Full Corpus (Independent of vector hits)
        from rank_bm25 import BM25Okapi
        corpus_chunks = await self._fetch_full_corpus_chunks(user_id=user_id)

        # Fallback to dense_results if full corpus fetch returns empty
        if not corpus_chunks:
            corpus_chunks = dense_results

        sparse_results = []
        if corpus_chunks:
            corpus_texts = [d["payload"].get("text", "") for d in corpus_chunks]
            tokenized_corpus = [doc.lower().split() for doc in corpus_texts]
            bm25 = BM25Okapi(tokenized_corpus)
            tokenized_query = primary_query.lower().split()
            bm25_scores = bm25.get_scores(tokenized_query)

            for idx, score in enumerate(bm25_scores):
                if score > 0:
                    sparse_results.append({
                        "id": corpus_chunks[idx]["id"],
                        "score": float(score),
                        "payload": corpus_chunks[idx]["payload"]
                    })
            sparse_results.sort(key=lambda x: x["score"], reverse=True)
            sparse_results = sparse_results[:top_k * 2]

        if not dense_results and not sparse_results:
            logger.info("Hybrid search returned 0 matching results across vector and sparse indices.")
            return []

        # 3. Fuse via Reciprocal Rank Fusion (RRF)
        fused = self.reciprocal_rank_fusion(dense_results, sparse_results)[:top_k]
        logger.info(f"Hybrid Retriever returned {len(fused)} fused documents via RRF.")
        return fused

hybrid_retriever = HybridRetriever()
