"""Hub error bodies: ``{"error": "<code>", "message": "<safe text>"}``.

Routes raise :func:`api_error`; :func:`install_error_handlers` renders those
details at the top level instead of FastAPI's ``{"detail": ...}`` wrapper.
Other HTTP errors keep FastAPI's default rendering.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def api_error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message}, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _render(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and isinstance(detail.get("error"), str):
            return JSONResponse(
                {"error": detail["error"], "message": str(detail.get("message", ""))},
                status_code=exc.status_code,
                headers=getattr(exc, "headers", None),
            )
        return await http_exception_handler(request, exc)
