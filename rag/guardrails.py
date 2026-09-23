import re
import logging
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# Prompt Injection & Jailbreak Regex Patterns
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+)?(?:system|previous)\s+prompts", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+in\s+developer\s+mode", re.IGNORECASE),
    re.compile(r"system\s+override", re.IGNORECASE),
    re.compile(r"reveal\s+your\s+(?:system\s+)?prompt", re.IGNORECASE),
    re.compile(r"dan\s+mode", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
]

class InputGuardrail:
    """
    Scans incoming queries for prompt injection attacks and safety violations.
    """
    def check_query(self, query: str) -> Tuple[bool, str]:
        if not query:
            return True, "Valid query"

        for pattern in INJECTION_PATTERNS:
            if pattern.search(query):
                logger.warning(f"Guardrail Alert: Prompt injection pattern detected in query: '{query}'")
                return False, "Prompt injection pattern detected."

        return True, "Query passed guardrail checks."

class RetrievalConfidenceGate:
    """
    Evaluates top reranked search results to ensure sufficient context confidence.
    """
    def __init__(self, min_confidence_threshold: float = 0.30):
        self.min_confidence_threshold = min_confidence_threshold

    def is_confident(self, reranked_docs: List[Dict[str, Any]]) -> bool:
        if not reranked_docs:
            return False

        top_score = reranked_docs[0].get("rerank_score", 0.0)
        logger.info(f"Retrieval Confidence Gate: Top score = {top_score} (Threshold = {self.min_confidence_threshold})")
        return top_score >= self.min_confidence_threshold

input_guardrail = InputGuardrail()
confidence_gate = RetrievalConfidenceGate(min_confidence_threshold=0.30)
