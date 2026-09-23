import logging
from typing import List, Dict, Any
from app.config import settings

logger = logging.getLogger(__name__)

class Reranker:
    def rerank(self, query: str, documents: List[Dict[str, Any]], top_n: int = 3) -> List[Dict[str, Any]]:
        """
        Reranks retrieved candidate document chunks against the user query.
        """
        if not documents:
            return []

        query_terms = set(query.lower().split())

        scored_docs = []
        for doc in documents:
            text = doc.get("payload", {}).get("text", "").lower()
            overlap_count = sum(1 for term in query_terms if term in text)
            rrf_score = doc.get("rrf_score", 0.5)

            # Combined relevance score
            relevance_score = round(rrf_score + (0.1 * overlap_count), 4)
            doc["rerank_score"] = relevance_score
            scored_docs.append(doc)

        scored_docs.sort(key=lambda x: x["rerank_score"], reverse=True)
        reranked = scored_docs[:top_n]
        logger.info(f"Reranker filtered top {len(reranked)} documents from {len(documents)} candidates.")
        return reranked

reranker = Reranker()
