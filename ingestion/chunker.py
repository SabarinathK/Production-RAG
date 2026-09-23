import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class TextChunker:
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 100):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_document(self, parsed_pages: List[Dict[str, Any]], doc_id: str, filename: str) -> List[Dict[str, Any]]:
        chunks = []
        chunk_idx = 0

        for page in parsed_pages:
            text = page.get("text", "")
            page_number = page.get("page_number", 1)

            words = text.split()
            if not words:
                continue

            i = 0
            while i < len(words):
                chunk_words = words[i:i + self.chunk_size]
                chunk_text = " ".join(chunk_words)

                chunks.append({
                    "chunk_id": f"{doc_id}_chunk_{chunk_idx}",
                    "document_id": doc_id,
                    "doc_id": doc_id,
                    "filename": filename,
                    "page_number": page_number,
                    "chunk_index": chunk_idx,
                    "text": chunk_text
                })

                chunk_idx += 1
                i += (self.chunk_size - self.chunk_overlap)

        logger.info(f"Chunker created {len(chunks)} chunks for doc '{doc_id}'.")
        return chunks

text_chunker = TextChunker()
