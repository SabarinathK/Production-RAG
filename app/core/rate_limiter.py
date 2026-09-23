import time
import logging
from fastapi import Request, HTTPException, status
from app.config import settings
from app.core.database import get_redis_client

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, requests_per_minute: int = settings.RATE_LIMIT_REQUESTS_PER_MINUTE):
        self.requests_per_minute = requests_per_minute

    async def _check_key(self, redis, key: str):
        current_time = time.time()
        one_minute_ago = current_time - 60

        async with redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, one_minute_ago)
            pipe.zcard(key)
            pipe.zadd(key, {str(current_time): current_time})
            pipe.expire(key, 60)
            results = await pipe.execute()

        request_count = results[1]
        if request_count >= self.requests_per_minute:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for '{key}'. Maximum {self.requests_per_minute} requests per minute allowed."
            )

    async def check_rate_limit(self, request: Request, identifier: str):
        if not settings.ENABLE_RATE_LIMITING:
            return

        try:
            redis = await get_redis_client()
            client_ip = request.client.host if request and request.client else "unknown"

            # 1. User-ID rate limit check
            if identifier:
                await self._check_key(redis, f"rate_limit:user:{identifier}")

            # 2. Per-IP rate limit ceiling check
            if client_ip and client_ip != "unknown":
                await self._check_key(redis, f"rate_limit:ip:{client_ip}")

        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Rate limiting check note: {e}")

rate_limiter = RateLimiter()

