"""
FastAPI application entry point.

Registers three exception handlers (ordered by specificity):

  1. RequestValidationError (400) -- Pydantic schema validation failures
  2. BaseAppException         (400-429) -- domain/business logic errors
  3. Exception                (500) -- safety net for programming errors

Plus:
  - CORSMiddleware (BACKEND_CORS_ORIGINS from config)
  - Lifespan (connect Redis on startup, dispose on shutdown)
  - Health check endpoint
  - All domain routers via api/v1/router.py

All error responses share the same JSON shape:
  {"success": false, "message": "...", "errors": {...}, "error_code": "..."}
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.exceptions import BaseAppException
from app.db.session import engine
from app.shared.schemas import SuccessResponse

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("sqlalchemy.engine").propagate = False

# ─── Lifespan (startup / shutdown) ───────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup: connect Redis (falls back to in-memory if unavailable).
    Shutdown: disconnect Redis and dispose database engine.
    """
    from app.core.redis_client import connect_redis, disconnect_redis

    logger.info("Starting %s", settings.APP_NAME)
    await connect_redis()

    yield

    logger.info("Shutting down")
    await disconnect_redis()
    await engine.dispose()


# ─── Application ──────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ─── Exception Handlers (order matters -- most specific first) ────────────────


# 1. Pydantic validation errors (400)
@app.exception_handler(RequestValidationError)
async def pydantic_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    """
    Override FastAPI's default 422 response with our standard error shape.

    Normalizes Pydantic errors to match BaseAppException.to_dict() so
    the frontend has one error format to parse, always.

    Field names are extracted from the Pydantic loc tuple, stripping
    FastAPI's source prefixes (body, query, path, header) to give
    clean field names regardless of where the parameter came from.
    """
    formatted_errors: dict[str, list[str]] = {}
    for error in exc.errors():
        field_path = ".".join(
            str(loc) for loc in error["loc"]
            if loc not in ("body", "query", "path", "header")
        ) or "payload"
        formatted_errors.setdefault(field_path, []).append(error["msg"])

    logger.debug(
        "Validation failed on %s %s: %d errors",
        request.method,
        request.url.path,
        len(formatted_errors),
    )

    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "message": "Schema validation failed for incoming payload parameters.",
            "errors": formatted_errors,
            "error_code": "schema_validation_error",
        },
    )


# 2. Application exceptions (400-429)
@app.exception_handler(BaseAppException)
async def app_exception_handler(
    request: Request,
    exc: BaseAppException,
):
    """
    Catches every BaseAppException raised anywhere in the application --
    domain services, repositories, middleware, dependencies -- and
    converts it to the standard JSON error response.

    log_context carries structured data for monitoring (e.g.
    property_id, user_id) but NEVER sensitive data (passwords,
    tokens).
    """
    logger.warning(
        "%s %s -> %s %s: %s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.__class__.__name__,
        exc.message,
        extra=exc.log_context,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


# 3. FastAPI default 404 -- normalise to our error shape
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """
    Catch FastAPI's built-in 404 responses and normalise them
    into our standard error response shape. Without this handler,
    a request to /nonexistent returns {"detail": "Not Found"} instead
    of our standard {"success": false, ...}.
    """
    return JSONResponse(
        status_code=404,
        content={
            "success": False,
            "message": "The requested resource was not found",
            "errors": {},
            "error_code": "not_found",
        },
    )


# 4. Unhandled exceptions (500) -- safety net
@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
):
    """
    Safety net: any exception NOT subclassing BaseAppException (e.g.
    a programming error, unexpected DB failure, or missing import) gets
    caught here so the client always receives JSON instead of an HTML
    traceback.

    Logs at ERROR level with full traceback for debugging.
    Returns a generic 500 to the client -- never leaks internal details.
    """
    logger.error(
        "Unhandled exception on %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "An unexpected error occurred",
            "errors": {},
            "error_code": "internal_error",
        },
    )


# ─── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Domain Routers (all routes under /api/v1) ───────────────────────────────

from app.api.v1.router import api_router  # noqa: E402

app.include_router(api_router, prefix="/api/v1")


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Confirms the app starts and is accepting requests."""
    return SuccessResponse.ok(
        data={"app": settings.APP_NAME},
        message="Healthy",
    )