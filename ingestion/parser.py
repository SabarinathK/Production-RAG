import logging
from typing import List, Dict, Any
from rag.pii_redactor import pii_redactor

logger = logging.getLogger(__name__)

class DocumentParser:
    def parse_bytes(self, file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
        """
        Parses document binary content and extracts text per page/section with PII scrubbing.
        """
        extracted_pages = []

        if filename.lower().endswith(".pdf"):
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    raw_text = page.get_text()
                    clean_text = pii_redactor.redact(raw_text)
                    if clean_text.strip():
                        extracted_pages.append({
                            "page_number": page_num + 1,
                            "text": clean_text
                        })
            except Exception as e:
                logger.warning(f"PyMuPDF parse error: {e}, falling back to utf-8 decode.")
                raw_text = file_bytes.decode("utf-8", errors="ignore")
                clean_text = pii_redactor.redact(raw_text)
                extracted_pages.append({"page_number": 1, "text": clean_text})
        elif filename.lower().endswith((".docx", ".doc")):
            try:
                import io
                import docx
                doc = docx.Document(io.BytesIO(file_bytes))
                paragraphs = [para.text for para in doc.paragraphs if para.text and para.text.strip()]
                full_text = "\n\n".join(paragraphs)
                clean_text = pii_redactor.redact(full_text)
                if clean_text.strip():
                    extracted_pages.append({"page_number": 1, "text": clean_text})
            except Exception as e:
                logger.warning(f"python-docx parse error: {e}, falling back to utf-8 decode.")
                raw_text = file_bytes.decode("utf-8", errors="ignore")
                clean_text = pii_redactor.redact(raw_text)
                extracted_pages.append({"page_number": 1, "text": clean_text})
        else:
            raw_text = file_bytes.decode("utf-8", errors="ignore")
            clean_text = pii_redactor.redact(raw_text)
            extracted_pages.append({"page_number": 1, "text": clean_text})

        logger.info(f"Document parser extracted {len(extracted_pages)} pages from '{filename}'.")
        return extracted_pages

document_parser = DocumentParser()
