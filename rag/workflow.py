import logging
from typing import TypedDict, List, Dict, Any, Optional
from langgraph.graph import StateGraph, END

from rag.query_rewriter import query_rewriter
from rag.semantic_cache import semantic_cache
from rag.hybrid_retriever import hybrid_retriever
from rag.reranker import reranker
from rag.generator import generator
from rag.pii_redactor import pii_redactor
from rag.guardrails import input_guardrail, confidence_gate

from app.core.telemetry import tracer

logger = logging.getLogger(__name__)

class RAGState(TypedDict):
    user_query: str
    user_id: Optional[str]
    clean_query: str
    expanded_queries: List[str]
    is_injection: bool
    is_low_confidence: bool
    cache_hit: bool
    retrieved_docs: List[Dict[str, Any]]
    reranked_docs: List[Dict[str, Any]]
    final_response: Dict[str, Any]

# -------------------------------------------------------------------
# LangGraph Node Handlers
# -------------------------------------------------------------------
def pii_redact_input_node(state: RAGState) -> Dict[str, Any]:
    with tracer.start_as_current_span("rag_node.pii_redact"):
        raw_query = state["user_query"]
        clean = pii_redactor.redact(raw_query)
        logger.info("Node [PII Redact]: Input scrubbed.")
        return {"clean_query": clean}

def guardrail_check_node(state: RAGState) -> Dict[str, Any]:
    with tracer.start_as_current_span("rag_node.guardrail_check"):
        clean_query = state["clean_query"]
        is_safe, reason = input_guardrail.check_query(clean_query)
        if not is_safe:
            logger.warning(f"Node [Guardrail]: Blocked query - {reason}")
            return {
                "is_injection": True,
                "final_response": {
                    "answer": "Request blocked: Input query violates security policy (prompt injection pattern detected).",
                    "citations": [],
                    "groundedness_score": 0.0
                }
            }
        logger.info("Node [Guardrail]: Query passed input safety checks.")
        return {"is_injection": False}

def query_rewrite_node(state: RAGState) -> Dict[str, Any]:
    with tracer.start_as_current_span("rag_node.query_rewrite"):
        clean_query = state["clean_query"]
        queries = query_rewriter.rewrite_and_expand(clean_query)
        logger.info(f"Node [Query Rewrite]: Expanded queries count = {len(queries)}")
        return {"expanded_queries": queries}

async def cache_check_node(state: RAGState) -> Dict[str, Any]:
    with tracer.start_as_current_span("rag_node.cache_check"):
        clean_query = state["clean_query"]
        user_id = state.get("user_id")
        cached = await semantic_cache.get(clean_query, user_id=user_id)
        if cached:
            logger.info("Node [Cache Check]: Cache HIT")
            return {"cache_hit": True, "final_response": cached}
        logger.info("Node [Cache Check]: Cache MISS")
        return {"cache_hit": False}

async def retrieve_node(state: RAGState) -> Dict[str, Any]:
    with tracer.start_as_current_span("rag_node.hybrid_retrieve") as span:
        queries = state["expanded_queries"]
        user_id = state.get("user_id")
        docs = await hybrid_retriever.search(queries, top_k=5, user_id=user_id)
        span.set_attribute("retrieved_doc_count", len(docs))
        logger.info(f"Node [Hybrid Retriever]: Retrieved {len(docs)} documents.")
        return {"retrieved_docs": docs}

def rerank_node(state: RAGState) -> Dict[str, Any]:
    with tracer.start_as_current_span("rag_node.rerank") as span:
        query = state["clean_query"]
        docs = state["retrieved_docs"]
        reranked = reranker.rerank(query, docs, top_n=3)
        span.set_attribute("reranked_doc_count", len(reranked))
        logger.info(f"Node [Reranker]: Top {len(reranked)} documents selected.")

        from app.core.telemetry import RETRIEVAL_SCORE
        RETRIEVAL_SCORE.observe(reranked[0].get("rerank_score", 0.0) if reranked else 0.0)

        # Check retrieval confidence gate
        confident = confidence_gate.is_confident(reranked)
        if not confident:
            logger.info("Node [Confidence Gate]: Retrieval confidence score below threshold.")
            return {
                "reranked_docs": reranked,
                "is_low_confidence": True,
                "final_response": {
                    "answer": "I do not have sufficient high-confidence document context to answer your query accurately.",
                    "citations": [],
                    "groundedness_score": 0.0
                }
            }

        return {"reranked_docs": reranked, "is_low_confidence": False}

async def generate_node(state: RAGState) -> Dict[str, Any]:
    with tracer.start_as_current_span("rag_node.generate"):
        query = state["clean_query"]
        docs = state["reranked_docs"]
        user_id = state.get("user_id")
        raw_output = await generator.generate_response(query, docs)

        from app.core.telemetry import GROUNDEDNESS_SCORE
        GROUNDEDNESS_SCORE.observe(raw_output.get("groundedness_score", 0.0))

        raw_output["answer"] = pii_redactor.redact(raw_output["answer"])
        await semantic_cache.set(query, raw_output, user_id=user_id)

        logger.info("Node [Generator]: Response created and cached.")
        return {"final_response": raw_output}

# -------------------------------------------------------------------
# Condition Routers
# -------------------------------------------------------------------
def guardrail_router(state: RAGState) -> str:
    if state.get("is_injection"):
        return "end"
    return "query_rewrite"

def cache_router(state: RAGState) -> str:
    if state.get("cache_hit"):
        return "end"
    return "retrieve"

def confidence_router(state: RAGState) -> str:
    if state.get("is_low_confidence"):
        return "end"
    return "generate"

# -------------------------------------------------------------------
# LangGraph Workflow Definition
# -------------------------------------------------------------------
def build_rag_workflow():
    workflow = StateGraph(RAGState)

    workflow.add_node("pii_redact", pii_redact_input_node)
    workflow.add_node("guardrail_check", guardrail_check_node)
    workflow.add_node("query_rewrite", query_rewrite_node)
    workflow.add_node("cache_check", cache_check_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("rerank", rerank_node)
    workflow.add_node("generate", generate_node)

    workflow.set_entry_point("pii_redact")
    workflow.add_edge("pii_redact", "guardrail_check")

    workflow.add_conditional_edges(
        "guardrail_check",
        guardrail_router,
        {
            "end": END,
            "query_rewrite": "query_rewrite "
        }
    )

    workflow.add_edge("query_rewrite", "cache_check")

    workflow.add_conditional_edges(
        "cache_check",
        cache_router,
        {
            "end": END,
            "retrieve": "retrieve"
        }
    )

    workflow.add_edge("retrieve", "rerank")

    workflow.add_conditional_edges(
        "rerank",
        confidence_router,
        {
            "end": END,
            "generate": "generate"
        }
    )

    workflow.add_edge("generate", END)

    return workflow.compile()

rag_orchestrator = build_rag_workflow()
