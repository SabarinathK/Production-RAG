import logging
from typing import List
from app.config import settings

logger = logging.getLogger(__name__)

class QueryRewriter:
    def rewrite_and_expand(self, original_query: str) -> List[str]:
        """
        Generates query variations and Hypothetical Document Embeddings (HyDE) text.
        """
        clean_query = original_query.strip()
        queries = [clean_query]

        # Generate expanded sub-queries
        if len(clean_query.split()) > 2:
            queries.append(f"Detailed overview of {clean_query}")
            queries.append(f"Key facts and specifications for {clean_query}")

        logger.info(f"Query rewriter expanded 1 query into {len(queries)} variations.")
        return queries

    def generate_hyde_doc(self, query: str) -> str:
        """
        Generates a hypothetical document passage answering the query for semantic vector matching.
        """
        return f"This passage directly explains {query}. It contains technical specifications, usage guidelines, and complete reference details regarding {query}."

query_rewriter = QueryRewriter()
