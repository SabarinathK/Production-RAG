import asyncio
import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from app.core import database
from app.core.database import AuditLog
from app.core.telemetry import REQUEST_COUNT

from opentelemetry import trace

logger = logging.getLogger(__name__)

async def _save_audit_log(user_id: str, trace_id: str, endpoint: str, method: str, status_code: int, ip_address: str, execution_time_ms: float, params: str):
    """Persist audit log entry using a fresh session (avoids asyncpg concurrent-operation conflicts)."""
    try:
        # Small yield to let the response complete before opening a new connection
        await asyncio.sleep(0)
        async with database.AsyncSessionLocal() as db:
            audit_entry = AuditLog(
                user_id=user_id,
                trace_id=trace_id,
                endpoint=endpoint,
                method=method,
                status_code=status_code,
                ip_address=ip_address,
                execution_time_ms=execution_time_ms,
                details=params
            )
            db.add(audit_entry)
            await db.commit()
    except Exception as e:
        logger.warning(f"Audit log insertion failed: {e}")

class AuditLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        execution_time_ms = round((time.time() - start_time) * 1000, 2)

        client_ip = request.client.host if request.client else "unknown"
        user_id = getattr(request.state, "user_id", "anonymous")

        span = trace.get_current_span()
        trace_id = format(span.get_span_context().trace_id, "032x") if span and span.get_span_context().trace_id else None

        # Record Prometheus metric for all HTTP requests
        try:
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=str(request.url.path),
                status=str(response.status_code)
            ).inc()
        except Exception:
            pass

        # Schedule audit log insertion in background task
        asyncio.create_task(_save_audit_log(
            user_id=user_id,
            trace_id=trace_id,
            endpoint=str(request.url.path),
            method=request.method,
            status_code=response.status_code,
            ip_address=client_ip,
            execution_time_ms=execution_time_ms,
            params=f"Params: {dict(request.query_params)}"
        ))

        return response


