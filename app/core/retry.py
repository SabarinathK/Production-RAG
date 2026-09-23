import asyncio
import random
import logging
import functools
from typing import Callable, Any, Tuple, Type

logger = logging.getLogger(__name__)

def async_retry(
    max_retries: int = 3,
    initial_delay: float = 0.2,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
) -> Callable:
    """
    Decorator for retrying async functions with exponential backoff and jitter.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            last_exception = None

            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(f"Function '{func.__name__}' failed after {max_retries} attempts: {e}")
                        raise e

                    sleep_time = delay
                    if jitter:
                        sleep_time += random.uniform(0, delay * 0.5)

                    logger.warning(
                        f"Function '{func.__name__}' attempt {attempt}/{max_retries} failed ({e}). "
                        f"Retrying in {sleep_time:.2f}s..."
                    )
                    await asyncio.sleep(sleep_time)
                    delay *= backoff_factor

            if last_exception:
                raise last_exception

        return wrapper
    return decorator
