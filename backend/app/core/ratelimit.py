"""Lightweight in-process rate limiter for abuse-sensitive endpoints.

Used as a FastAPI dependency: `Depends(rate_limit("bucket", limit, window_sec))`. Keys by
client IP (honouring X-Forwarded-For behind the platform proxy) + bucket, using a sliding
window. Returns HTTP 429 with a Retry-After header when the limit is exceeded.

NOTE: state is per-process (in-memory). This is sufficient for the current single-worker
deployment and complements the DB-backed login lockout. For horizontal scaling, swap the
backing store for Redis without changing call sites.
"""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_BUCKETS: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, limit: int, window_sec: int):
    async def _dep(request: Request) -> None:
        key = f"{bucket}:{_client_ip(request)}"
        now = time.monotonic()
        cutoff = now - window_sec
        dq = _BUCKETS[key]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry_after = max(int(window_sec - (now - dq[0])) + 1, 1)
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please slow down and try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )
        dq.append(now)

    return _dep
