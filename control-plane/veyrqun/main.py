import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, config, service, simulator
from .counters import CounterUnavailable
from .routes import routers
from .service import GovernanceError


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
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.get_settings().origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
)


@app.exception_handler(GovernanceError)
async def governance_error_handler(_: Request, exc: GovernanceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(CounterUnavailable)
async def counters_down_handler(_: Request, exc: CounterUnavailable):
    return JSONResponse(status_code=503, content={"detail": "state store unavailable"})


for router in routers:
    app.include_router(router)
