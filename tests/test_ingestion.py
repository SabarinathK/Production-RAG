import pytest
from ingestion.parser import document_parser
from ingestion.chunker import text_chunker

def test_document_parser_and_chunker():
    sample_text = b"This is page 1 content of test document.\n" * 50
    pages = document_parser.parse_bytes(sample_text, "sample.txt")
    assert len(pages) >= 1

    chunks = text_chunker.chunk_document(pages, doc_id="doc_test_123", filename="sample.txt")
    assert len(chunks) > 0
    assert chunks[0]["document_id"] == "doc_test_123"
    assert "chunk_id" in chunks[0]
