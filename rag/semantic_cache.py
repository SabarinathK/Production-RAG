import hashlib
import json
import logging
from typing import Optional, Dict, Any
from app.core.database import get_redis_client
from app.core.telemetry import CACHE_HITS, CACHE_MISSES
from app.core.retry import async_retry

logger = logging.getLogger(__name__)

class SemanticCache:
    def _compute_key(self, query: str, user_id: Optional[str] = None) -> str:
        normalized = query.strip().lower()
        query_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        user_prefix = user_id if user_id else "global"
        return f"semantic_cache:{user_prefix}:{query_hash}"

    @async_retry(max_retries=2, initial_delay=0.1, backoff_factor=1.5)
    async def get(self, query: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        try:
            redis = await get_redis_client()
            key = self._compute_key(query, user_id=user_id)
            cached_data = await redis.get(key)
            if cached_data:
                CACHE_HITS.inc()
                logger.info(f"Semantic Cache HIT for query: '{query[:30]}...' (user: {user_id})")
                return json.loads(cached_data)
        except Exception as e:
            logger.warning(f"Semantic cache read fallback: {e}")

        CACHE_MISSES.inc()
        return None

    @async_retry(max_retries=2, initial_delay=0.1, backoff_factor=1.5)
    async def set(self, query: str, response_data: Dict[str, Any], ttl_seconds: int = 3600, user_id: Optional[str] = None):
        try:
            redis = await get_redis_client()
            key = self._compute_key(query, user_id=user_id)
            await redis.set(key, json.dumps(response_data), ex=ttl_seconds)
            logger.info(f"Semantic Cache STORED for query: '{query[:30]}...' (user: {user_id})")
        except Exception as e:
            logger.warning(f"Semantic cache write fallback: {e}")

    @async_retry(max_retries=2, initial_delay=0.1, backoff_factor=1.5)
    async def clear_cache(self, user_id: Optional[str] = None):
        try:
            redis = await get_redis_client()
            pattern = f"semantic_cache:{user_id}:*" if user_id else "semantic_cache:*"
            keys = await redis.keys(pattern)
            if keys:
                await redis.delete(*keys)
                logger.info(f"Cleared {len(keys)} semantic cache keys for pattern '{pattern}'")
        except Exception as e:
            logger.warning(f"Semantic cache clear fallback: {e}")

semantic_cache = SemanticCache()
