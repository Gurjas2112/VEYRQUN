import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, config, service, simulator
from .auth.directory import DirectoryError
from .counters import CounterUnavailable
from .routes import routers
from .security import BodySizeLimit, SecurityHeaders
from .service import GovernanceError

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = config.get_settings()
    service.default_workspace()  # seeds the default workspace; fails fast if Postgres is unreachable
    task = asyncio.create_task(simulator.run()) if settings.veyrqun_simulator else None
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title=config.APP_TITLE,
    version=__version__,
    description="Control plane for VEYRQUN: per-agent policies (OPA/Rego), live spend and rate counters (Redis), "
    "revocation and fleet emergency stop, approvals, agent credentials, signed policy bundles and a "
    "SHA-256 hash-chained audit ledger (PostgreSQL).",
    lifespan=lifespan,
    docs_url=None if config.get_settings().veyrqun_env == "production" else "/docs",
    redoc_url=None if config.get_settings().veyrqun_env == "production" else "/redoc",
)

app.add_middleware(BodySizeLimit)
app.add_middleware(SecurityHeaders)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.get_settings().origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
    expose_headers=["X-Veyrqun-Step-Up"],
)


@app.exception_handler(GovernanceError)
async def governance_error_handler(_: Request, exc: GovernanceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(DirectoryError)
async def directory_error_handler(_: Request, exc: DirectoryError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(CounterUnavailable)
async def counters_down_handler(_: Request, exc: CounterUnavailable):
    return JSONResponse(status_code=503, content={"detail": "state store unavailable"})


for router in routers:
    app.include_router(router)
