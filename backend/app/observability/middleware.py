import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from app.observability.context import accept_request_id, correlate

logger = logging.getLogger("app.http")

REQUEST_ID_HEADER = "X-Request-ID"


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Give every request an id, log its outcome, and return the id to the caller."""
    request_id = accept_request_id(request.headers.get(REQUEST_ID_HEADER))
    started = time.perf_counter()
    with correlate(request_id=request_id):
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra={"method": request.method, "path": request.url.path},
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        level = logging.ERROR if response.status_code >= 500 else logging.INFO
        logger.log(
            level,
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
