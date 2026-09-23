import uuid
import logging
from typing import Dict, Any
from ingestion.celery_app import celery_app
from ingestion.parser import document_parser
from ingestion.chunker import text_chunker
from app.config import settings
from ingestion.storage import storage_manager

logger = logging.getLogger(__name__)


def _update_document_status(doc_id: str, status: str, chunk_count: int = 0, error_detail: str = None):
    """Synchronously update DocumentMetadata status from within a Celery task."""
    import asyncio
    from app.config import settings
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    from app.core.database import DocumentMetadata
    from sqlalchemy import select

    async def _update():
        engine = create_async_engine(settings.get_database_url(), poolclass=NullPool)
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with session_factory() as db:
                stmt = select(DocumentMetadata).where(DocumentMetadata.id == doc_id)
                res = await db.execute(stmt)
                doc = res.scalar_one_or_none()
                if doc:
                    doc.status = status
                    if chunk_count:
                        doc.chunk_count = chunk_count
                    if error_detail:
                        doc.error_detail = error_detail
                    await db.commit()
                    logger.info(f"DocumentMetadata [{doc_id}] status updated to '{status}'.")
                else:
                    logger.warning(f"DocumentMetadata [{doc_id}] not found for status update.")
        finally:
            await engine.dispose()

    # Celery workers run in a synchronous context; run async DB update safely with asyncio.run
    try:
        asyncio.run(_update())
    except Exception as e:
        logger.error(f"Failed to update DocumentMetadata status for doc '{doc_id}': {e}")


@celery_app.task(bind=True, name="ingestion.process_document_task", max_retries=3, default_retry_delay=10)
def process_document_task(self, s3_key: str, filename: str, doc_id: str, user_id: str = "anonymous") -> Dict[str, Any]:
    # Compute stable task_id regardless of whether called via Celery or synchronously
    task_id = (self.request.id if hasattr(self, "request") and self.request and self.request.id else None) or f"sync_{doc_id}"
    logger.info(f"Celery Task [{task_id}]: Ingesting document '{filename}' (ID: {doc_id}, User: {user_id}) from s3_key '{s3_key}'")

    try:
        file_bytes = storage_manager.download_bytes(s3_key)

        # 1. Parse document text
        parsed_pages = document_parser.parse_bytes(file_bytes, filename)

        # 2. Split into chunks
        chunks = text_chunker.chunk_document(parsed_pages, doc_id, filename)

        # 3. Embed & index into Qdrant
        from rag.hybrid_retriever import embedding_generator
        from app.core.database import get_qdrant_client
        qdrant = get_qdrant_client()
        points = []

        try:
            from qdrant_client.models import PointStruct
            import asyncio

            async def embed_all():
                embedded = []
                for chunk in chunks:
                    vector = await embedding_generator.embed_query(chunk["text"])
                    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk["chunk_id"]))
                    chunk_payload = dict(chunk)
                    chunk_payload["user_id"] = user_id
                    embedded.append(PointStruct(id=point_id, vector=vector, payload=chunk_payload))
                return embedded

            # Run async embedding inside the sync Celery worker
            points = asyncio.run(embed_all())

            if points:
                qdrant.upsert(
                    collection_name=settings.QDRANT_COLLECTION,
                    points=points
                )
                logger.info(f"Qdrant successfully indexed {len(points)} vector chunks.")
        except Exception as e:
            logger.error(f"Error during Qdrant indexing in Celery task: {e}")
            raise

        # 4. Mark document as COMPLETED with chunk count
        _update_document_status(doc_id, "COMPLETED", chunk_count=len(chunks))

        return {
            "task_id": task_id,
            "doc_id": doc_id,
            "filename": filename,
            "chunks_indexed": len(chunks),
            "status": "COMPLETED"
        }

    except Exception as exc:
        logger.error(f"Celery Task [{task_id}]: Processing failed for doc '{doc_id}': {exc}")
        # Mark document as FAILED with error detail
        _update_document_status(doc_id, "FAILED", error_detail=str(exc))
        # Retry with exponential backoff (up to max_retries)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries * 10)
