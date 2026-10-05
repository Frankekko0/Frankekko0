"""Application errors and uniform error responses.

Users never see raw technical errors. Every error response has the shape::

    {"error": {"code": "not_found", "message": "Human readable message", "details": ...}}

Unexpected exceptions are logged with full context server-side and returned as a generic
message with a request id the user can quote.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

log = get_logger(__name__)


class AppError(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"
    message: str = "La richiesta non è valida."

    def __init__(self, message: str | None = None, *, code: str | None = None, details: Any = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        if code:
            self.code = code
        self.details = details


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "La risorsa richiesta non esiste o non è più disponibile."


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "not_authenticated"
    message = "Sessione non valida o scaduta. Effettua di nuovo l'accesso."


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "Non hai i permessi per eseguire questa operazione."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "La risorsa esiste già."


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Troppe richieste in poco tempo. Riprova tra qualche secondo."


class InsufficientDataError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "insufficient_data"
    message = "Non ci sono abbastanza dati per stimare con affidabilità il prezzo di mercato."


class ProviderUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "provider_unavailable"
    message = "La sorgente dati del marketplace non è raggiungibile al momento. Riprova più tardi."


def _payload(code: str, message: str, details: Any = None, request_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    if request_id:
        body["request_id"] = request_id
    return {"error": body}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


_VALIDATION_MESSAGES = {
    "missing": "campo obbligatorio",
    "string_too_short": "valore troppo corto",
    "string_too_long": "valore troppo lungo",
    "greater_than_equal": "valore troppo basso",
    "less_than_equal": "valore troppo alto",
    "greater_than": "valore troppo basso",
    "value_error": "valore non valido",
}


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        headers = {"Retry-After": "30"} if isinstance(exc, RateLimitedError) else None
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.code, exc.message, exc.details, _request_id(request)),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
            fields.append(
                {"field": loc, "message": _VALIDATION_MESSAGES.get(err.get("type", ""), err.get("msg"))}
            )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_payload(
                "validation_error", "Alcuni campi non sono validi.", fields, _request_id(request)
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        messages = {
            404: ("not_found", "La risorsa richiesta non esiste."),
            405: ("method_not_allowed", "Operazione non consentita."),
            401: ("not_authenticated", AuthenticationError.message),
            403: ("forbidden", PermissionDeniedError.message),
        }
        code, message = messages.get(exc.status_code, ("http_error", "Si è verificato un errore."))
        return JSONResponse(
            status_code=exc.status_code, content=_payload(code, message, None, _request_id(request))
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("api.unhandled_error", path=request.url.path, method=request.method)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload(
                "internal_error",
                "Si è verificato un errore imprevisto. Il problema è stato registrato: riprova tra poco.",
                None,
                _request_id(request),
            ),
        )
