"""Bound public Core JSON requests, including chunked uploads."""

from fastapi import HTTPException
from starlette.responses import JSONResponse

from aog.bridge import MAX_BODY


class BodyLimit:
    def __init__(self, app, limit=MAX_BODY):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        try:
            if int(dict(scope.get("headers", [])).get(b"content-length", b"0")) > self.limit:
                return await JSONResponse({"detail": "Request body is too large"}, 413)(scope, receive, send)
        except ValueError:
            pass
        size = 0

        async def bounded_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > self.limit:
                raise HTTPException(413, "Request body is too large")
            return message

        return await self.app(scope, bounded_receive, send)
