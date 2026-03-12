import asyncio
import logging

log = logging.getLogger("pepper.retry")


async def retry_async(coro_func, *args, retries=3,
                      backoff=1.0, max_backoff=10.0, **kwargs):
    """Вызвать async-функцию с ретраями и экспоненциальным backoff."""
    last_exc = None
    delay = backoff
    for attempt in range(1, retries + 1):
        try:
            return await coro_func(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt < retries:
                log.warning(
                    "Attempt %d/%d failed for %s: %s. Retry in %.1fs",
                    attempt, retries, coro_func.__name__, e, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_backoff)
    raise last_exc