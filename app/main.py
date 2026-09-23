import uuid
import os
import time
import logging
import hashlib
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, Response, StreamingResponse, FileResponse

from pydantic import BaseModel, Field
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import select, delete
from opentelemetry import trace

from app.config import settings
from app.core import database
from app.core.database import (
    init_db, init_qdrant_collection, get_db, DocumentMetadata, User,
    check_db_health, check_qdrant_health, check_redis_health, get_qdrant_client
)
from app.core.auth import (
    get_current_user, create_access_token, hash_password, verify_password,
    revoke_token, security, UserPayload
)
from fastapi.security import HTTPAuthorizationCredentials
from app.core.rate_limiter import rate_limiter
from app.core.audit import AuditLoggerMiddleware
from app.core.telemetry import setup_telemetry, REQUEST_COUNT, QUERY_DURATION
from rag.workflow import rag_orchestrator
from rag.generator import generator
from ingestion.storage import storage_manager
from ingestion.tasks import process_document_task

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("prod_rag_api")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME}...")
    await init_db()
    init_qdrant_collection()
    setup_telemetry(app)
    logger.info("Startup complete. System ready.")
    yield
    logger.info("Shutting down application...")

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description="Enterprise Production RAG Architecture API Gateway & Application",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # Set specific origins in production .env, never "*" with credentials
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

# Add Audit Logging Middleware
app.add_middleware(AuditLoggerMiddleware)

# -------------------------------------------------------------------
# Global Exception Handler with OTel Trace Correlation
# -------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    span = trace.get_current_span()
    trace_id = format(span.get_span_context().trace_id, "032x") if span and span.get_span_context().trace_id else uuid.uuid4().hex
    logger.error(f"Unhandled Exception [Trace ID: {trace_id}]: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal Server Error",
            "detail": str(exc),
            "trace_id": trace_id
        }
    )

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, json_schema_extra={"example": "john_doe"})
    password: str = Field(..., min_length=6, json_schema_extra={"example": "SecurePass123!"})
    email: Optional[str] = Field(default=None, json_schema_extra={"example": "john@example.com"})

class TokenRequest(BaseModel):
    user_id: Optional[str] = Field(default=None, json_schema_extra={"example": "usr_123"})
    username: str = Field(..., json_schema_extra={"example": "tester"})
    password: str = Field(..., min_length=1, json_schema_extra={"example": "password"})

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600

class QueryRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        json_schema_extra={"example": "What is the architecture of the async document ingestion pipeline?"}
    )


class CitationSchema(BaseModel):
    citation_id: str
    document_name: str
    page_number: int
    snippet: str

class QueryResponse(BaseModel):
    query: str
    answer: str
    citations: List[CitationSchema]
    cache_hit: bool
    groundedness_score: float
    is_llm_generated: bool = False
    model_used: str = "synthesis_fallback"
    execution_time_ms: float


class UploadResponse(BaseModel):
    task_id: str
    doc_id: str
    filename: str
    s3_uri: str
    status: str

class DocumentItemSchema(BaseModel):
    id: str
    filename: str
    s3_key: str
    file_size_bytes: int
    chunk_count: int
    status: str
    created_at: str

# -------------------------------------------------------------------
# Static Files & Dashboard UI
# -------------------------------------------------------------------
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", include_in_schema=False)
async def root_frontend():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": f"Welcome to {settings.APP_NAME}. Visit /docs for OpenAPI specifications."}

# -------------------------------------------------------------------
# System & Diagnostics Endpoints
# -------------------------------------------------------------------

@app.get("/health", tags=["System"])
async def health_check():
    db_ok = await check_db_health()
    qdrant_ok = check_qdrant_health()
    redis_ok = await check_redis_health()

    # All three core services must be healthy
    healthy = db_ok and qdrant_ok and redis_ok
    status_code = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if healthy else "unhealthy",
            "app_name": settings.APP_NAME,
            "environment": settings.APP_ENV,
            "services": {
                "database": "ok" if db_ok else "unreachable",
                "vector_db": "ok" if qdrant_ok else "unreachable",
                "redis": "ok" if redis_ok else "unreachable"
            }
        }
    )

@app.get("/metrics", tags=["System"])
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED, tags=["Authentication"])
async def register_user(req: RegisterRequest):
    async with database.AsyncSessionLocal() as session:
        existing_stmt = select(User).where(User.username == req.username)
        res = await session.execute(existing_stmt)
        if res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Username '{req.username}' is already registered"
            )
        
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        pwd_hash = hash_password(req.password)
        new_user = User(
            id=user_id,
            username=req.username,
            email=req.email,
            password_hash=pwd_hash,
            role="user",  # Hardcoded server-side: self-registration is strictly 'user' role
            is_active=True
        )
        session.add(new_user)
        await session.commit()
        return {
            "user_id": user_id,
            "username": req.username,
            "role": new_user.role,
            "message": "User registered successfully"
        }


@app.post("/api/v1/auth/token", response_model=TokenResponse, tags=["Authentication"])
async def generate_token(req: TokenRequest):
    async with database.AsyncSessionLocal() as session:
        stmt = select(User).where(User.username == req.username)
        res = await session.execute(stmt)
        user = res.scalars().first()

        # Seed/auto-create tester user if missing (strictly gated for development environment only)
        if not user and req.username == "tester" and settings.APP_ENV == "development":
            user = User(
                id="usr_tester",
                username="tester",
                email="tester@example.com",
                password_hash=hash_password(req.password or "password"),
                role="admin",
                is_active=True
            )
            session.add(user)
            await session.commit()
        
        if not user or not verify_password(req.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password"
            )

        token = create_access_token(user_id=user.id, username=user.username, role=user.role)
        return TokenResponse(access_token=token)


@app.post("/api/v1/auth/logout", tags=["Authentication"])
async def logout(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    current_user: UserPayload = Depends(get_current_user)
):
    if credentials and credentials.credentials:
        await revoke_token(credentials.credentials)
    return {"message": "Successfully logged out"}


@app.get("/api/v1/auth/me", tags=["Authentication"])
async def get_me(current_user: UserPayload = Depends(get_current_user)):
    return {
        "user_id": current_user.user_id,
        "username": current_user.username,
        "role": current_user.role
    }

# -------------------------------------------------------------------
# RAG Chat Query Endpoints (Unary & Streaming)
# -------------------------------------------------------------------
@app.post("/api/v1/chat/query", response_model=QueryResponse, tags=["RAG Application"])
async def chat_query(
    req: QueryRequest,
    request: Request,
    current_user: UserPayload = Depends(get_current_user)
):
    request.state.user_id = current_user.user_id
    await rate_limiter.check_rate_limit(request, current_user.user_id)

    start_time = time.time()

    initial_state = {"user_query": req.query, "user_id": current_user.user_id}
    result_state = await rag_orchestrator.ainvoke(initial_state)

    execution_time_ms = round((time.time() - start_time) * 1000, 2)
    QUERY_DURATION.observe(execution_time_ms / 1000.0)

    final_resp = result_state.get("final_response", {})
    cache_hit = result_state.get("cache_hit", False)

    return QueryResponse(
        query=req.query,
        answer=final_resp.get("answer", "No answer generated."),
        citations=final_resp.get("citations", []),
        cache_hit=cache_hit,
        groundedness_score=final_resp.get("groundedness_score", 0.0),
        is_llm_generated=final_resp.get("is_llm_generated", False),
        model_used=final_resp.get("model_used", "synthesis_fallback"),
        execution_time_ms=execution_time_ms
    )


@app.post("/api/v1/chat/query/stream", tags=["RAG Application"])
async def chat_query_stream(
    req: QueryRequest,
    request: Request,
    current_user: UserPayload = Depends(get_current_user)
):
    request.state.user_id = current_user.user_id
    await rate_limiter.check_rate_limit(request, current_user.user_id)

    initial_state = {"user_query": req.query, "user_id": current_user.user_id}
    result_state = await rag_orchestrator.ainvoke(initial_state)

    if result_state.get("cache_hit"):
        cached_answer = result_state.get("final_response", {}).get("answer", "")
        return StreamingResponse(
            generator.generate_cached_stream(cached_answer),
            media_type="text/event-stream"
        )

    reranked_docs = result_state.get("reranked_docs", [])
    return StreamingResponse(
        generator.generate_response_stream(req.query, reranked_docs),
        media_type="text/event-stream"
    )

# -------------------------------------------------------------------
# Async Document Ingestion & Management Endpoints
# -------------------------------------------------------------------
@app.post("/api/v1/documents/upload", response_model=UploadResponse, tags=["Async Ingestion"])
async def upload_document(
    file: UploadFile = File(...),
    request: Request = None,
    current_user: UserPayload = Depends(get_current_user)
):
    if request:
        request.state.user_id = current_user.user_id
        await rate_limiter.check_rate_limit(request, current_user.user_id)

    # MIME type allowlist validation
    from rag.hybrid_retriever import ALLOWED_MIME_TYPES
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File type '{file.content_type}' is not supported. Allowed types: PDF, plain text, DOCX, PPTX, XLSX, CSV, Markdown."
        )

    file_bytes = await file.read()

    # Upload file size limit validation
    if len(file_bytes) > settings.MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Uploaded file size ({len(file_bytes)} bytes) exceeds maximum limit of {settings.MAX_UPLOAD_SIZE_BYTES} bytes."
        )

    content_hash = hashlib.sha256(file_bytes).hexdigest()

    # De-duplication check per user
    async with database.AsyncSessionLocal() as db:
        stmt = select(DocumentMetadata).where(
            DocumentMetadata.content_hash == content_hash,
            DocumentMetadata.user_id == current_user.user_id
        )
        res = await db.execute(stmt)
        existing_doc = res.scalar_one_or_none()

        if existing_doc:
            logger.info(f"Duplicate document upload detected for user {current_user.user_id} (Hash: {content_hash}). Skipping re-ingestion.")
            return UploadResponse(
                task_id="duplicate_skipped",
                doc_id=existing_doc.id,
                filename=existing_doc.filename,
                s3_uri=f"s3://{settings.S3_BUCKET_NAME}/{existing_doc.s3_key}",
                status="DUPLICATE_SKIPPED"
            )

    doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    s3_key = f"uploads/{doc_id}_{file.filename}"

    # 1. Upload binary file to S3 / Storage
    s3_uri = storage_manager.upload_bytes(file_bytes, s3_key)

    # 2. Store Metadata in Database
    async with database.AsyncSessionLocal() as db:
        meta = DocumentMetadata(
            id=doc_id,
            user_id=current_user.user_id,
            filename=file.filename,
            s3_key=s3_key,
            file_size_bytes=len(file_bytes),
            content_hash=content_hash,
            status="PROCESSING"
        )
        db.add(meta)
        await db.commit()

    # 3. Queue task passing s3_key reference & user_id
    try:
        task = process_document_task.delay(s3_key, file.filename, doc_id, user_id=current_user.user_id)
        task_id = task.id
    except Exception as e:
        logger.warning(f"RabbitMQ/Celery enqueue fallback (running task synchronously): {e}")
        sync_res = process_document_task(s3_key, file.filename, doc_id, user_id=current_user.user_id)
        task_id = "sync_" + doc_id

    return UploadResponse(
        task_id=task_id,
        doc_id=doc_id,
        filename=file.filename,
        s3_uri=s3_uri,
        status="PROCESSING"
    )

@app.get("/api/v1/documents", response_model=List[DocumentItemSchema], tags=["Async Ingestion"])
async def list_documents(
    current_user: UserPayload = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=500, description="Maximum number of documents to return"),
    offset: int = Query(default=0, ge=0, description="Number of documents to skip (for pagination)"),
):
    async with database.AsyncSessionLocal() as db:
        stmt = (
            select(DocumentMetadata)
            .where(DocumentMetadata.user_id == current_user.user_id)
            .order_by(DocumentMetadata.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        res = await db.execute(stmt)
        docs = res.scalars().all()
        return [
            DocumentItemSchema(
                id=d.id,
                filename=d.filename,
                s3_key=d.s3_key,
                file_size_bytes=d.file_size_bytes,
                chunk_count=d.chunk_count,
                status=d.status,
                created_at=d.created_at.isoformat()
            )
            for d in docs
        ]

@app.delete("/api/v1/documents/{doc_id}", tags=["Async Ingestion"])
async def delete_document(doc_id: str, current_user: UserPayload = Depends(get_current_user)):
    s3_key = None
    async with database.AsyncSessionLocal() as db:
        stmt = select(DocumentMetadata).where(DocumentMetadata.id == doc_id)
        res = await db.execute(stmt)
        doc = res.scalar_one_or_none()

        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document with ID '{doc_id}' not found.")

        if doc.user_id and doc.user_id != current_user.user_id and getattr(current_user, 'role', 'user') != 'admin':
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this document.")

        s3_key = doc.s3_key
        await db.delete(doc)
        await db.commit()

    if s3_key:
        storage_manager.delete_file(s3_key)

    try:
        client = get_qdrant_client()
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        client.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=Filter(
                should=[
                    FieldCondition(key="document_id", match=MatchValue(value=doc_id)),
                    FieldCondition(key="doc_id", match=MatchValue(value=doc_id))
                ]
            )
        )
    except Exception as e:
        logger.warning(f"Note during Qdrant points deletion for doc '{doc_id}': {e}")

    try:
        from rag.semantic_cache import semantic_cache
        await semantic_cache.clear_cache(current_user.user_id)
        await semantic_cache.clear_cache(None)
    except Exception as e:
        logger.warning(f"Note during cache invalidation for doc '{doc_id}': {e}")

    return {"doc_id": doc_id, "status": "DELETED", "message": f"Document '{doc_id}' successfully deleted."}

@app.get("/api/v1/documents/status/{task_id}", tags=["Async Ingestion"])
async def get_task_status(task_id: str):
    if task_id.startswith("sync_"):
        return {"task_id": task_id, "status": "COMPLETED", "result": "Processed synchronously"}
    if task_id == "duplicate_skipped":
        return {"task_id": task_id, "status": "COMPLETED", "result": "Duplicate file skipped"}

    try:
        from celery.result import AsyncResult
        res = AsyncResult(task_id, app=process_document_task.app)
        st = res.status
        if st == "PENDING":
            return {
                "task_id": task_id,
                "status": "PENDING",
                "result": None,
                "warning": "Task is queued. Verify Celery worker service is running."
            }
        return {
            "task_id": task_id,
            "status": st,
            "result": res.result if res.ready() else None
        }
    except Exception as e:
        return {"task_id": task_id, "status": "UNKNOWN", "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
