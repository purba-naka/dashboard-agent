"""Error domain Studio dan pemetaannya ke envelope JSON.

Envelope: ``{"error": {"code": str, "message": str, "details": dict}}``.
``StudioError`` tidak bergantung pada FastAPI sehingga dapat di-raise dari
modul mana pun (core, data, store); hanya ``register_error_handlers`` yang
menyentuh FastAPI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI


class StudioError(Exception):
    """Kelas dasar semua error domain Studio."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        http_status: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details) if details else {}
        self.http_status = http_status

    def to_dict(self) -> dict[str, Any]:
        """Isi objek `error` pada envelope (juga dipakai tool agent: `{"ok": False, "error": ...}`)."""
        return {"code": self.code, "message": self.message, "details": self.details}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r}, http_status={self.http_status})"


def error_envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


_HTTP_STATUS_CODES = {
    400: "BAD_REQUEST",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
}


def register_error_handlers(app: FastAPI) -> None:
    """Pasang exception handler sehingga semua error API memakai envelope yang sama."""
    from fastapi import Request
    from fastapi.encoders import jsonable_encoder
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    async def studio_error_handler(_: Request, exc: StudioError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=jsonable_encoder({"error": exc.to_dict()}),
        )

    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                "VALIDATION_ERROR",
                "Request tidak valid.",
                {"errors": jsonable_encoder(exc.errors())},
            ),
        )

    async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_STATUS_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code
        details = {} if isinstance(exc.detail, str) else {"detail": jsonable_encoder(exc.detail)}
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(code, message, details),
            headers=getattr(exc, "headers", None),
        )

    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_envelope("INTERNAL_ERROR", "Terjadi kesalahan internal."),
        )

    app.add_exception_handler(StudioError, studio_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
