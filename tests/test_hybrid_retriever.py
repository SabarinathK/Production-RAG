import pytest
from rag.hybrid_retriever import HybridRetriever

def test_reciprocal_rank_fusion_logic():
    retriever = HybridRetriever()

    dense_results = [
        {"id": "doc1", "score": 0.95, "payload": {"text": "Vector match alpha"}},
        {"id": "doc2", "score": 0.80, "payload": {"text": "Vector match beta"}}
    ]
    sparse_results = [
        {"id": "doc3", "score": 4.50, "payload": {"text": "Exact keyword match gamma"}},
        {"id": "doc1", "score": 2.10, "payload": {"text": "Vector match alpha"}}
    ]

    fused = retriever.reciprocal_rank_fusion(dense_results, sparse_results, k=60)

    # doc1 is in both dense and sparse, so its RRF score is higher
    assert len(fused) == 3
    assert fused[0]["id"] == "doc1"
    # doc3 (keyword match missed by dense) must be present in fused results
    doc_ids = [d["id"] for d in fused]
    assert "doc3" in doc_ids
