"""FastAPI entrypoint.

Owner: Stream A.

Startup prints a non-secret mode summary and refuses to start on unsafe configuration.
The process listens on 0.0.0.0 inside its container; host exposure is restricted to
127.0.0.1 by docker-compose.yml.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import router as api_router
from app.config import ConfigurationError, get_modes, get_settings
from app.errors import ErrorCode, TravelOpsError, error_payload
from app.observability.logging import configure_logging, correlation_id_var, get_logger

API_PREFIX = "/api/v1"
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        modes = get_modes()
    except ConfigurationError as exc:
        # Fail closed and say exactly why.
        log.error("startup_refused", reason=str(exc))
        raise

    log.info(
        "startup",
        app_env=settings.app_env.value,
        **{k: v for k, v in modes.to_dict().items() if k != "degradations"},
    )
    for degradation in modes.degradations:
        log.warning("degraded_mode", detail=degradation)
    if not modes.workflow_executable:
        log.warning(
            "workflow_execution_blocked",
            detail="assurance config unavailable; no action can be authorised",
        )

    yield

    from app.db.session import dispose_engine

    await dispose_engine()
    log.info("shutdown")


app = FastAPI(
    title="TravelOps AI",
    version="0.1.0",
    summary="Autonomous operating layer for airline disruption recovery",
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Local development only. The frontend is served from a loopback port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):  # noqa: ANN001, ANN201
    correlation_id = request.headers.get("X-Correlation-Id") or uuid.uuid4().hex
    token = correlation_id_var.set(correlation_id)
    try:
        response = await call_next(request)
    finally:
        correlation_id_var.reset(token)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


@app.exception_handler(TravelOpsError)
async def handle_travelops_error(request: Request, exc: TravelOpsError) -> JSONResponse:
    correlation_id = correlation_id_var.get()
    log.warning(
        "request_failed",
        error_code=exc.code,
        outcome="error",
        path=request.url.path,
        detail=exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.code, exc.message, correlation_id, exc.details),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_payload(
            ErrorCode.VALIDATION_FAILED,
            "Request validation failed",
            correlation_id_var.get(),
            {"errors": exc.errors()},
        ),
    )


app.include_router(api_router, prefix=API_PREFIX)
