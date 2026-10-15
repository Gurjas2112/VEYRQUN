import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, config, simulator
from .db import init_db
from .routes import routers
from .service import GovernanceError


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = asyncio.create_task(simulator.run()) if config.simulator_enabled() else None
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title=config.APP_TITLE,
    version=__version__,
    description="Per-agent permissions, real-time spend caps, revocation, fleet emergency stop "
    "and a SHA-256 hash-chained audit ledger for autonomous financial agents.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(GovernanceError)
async def governance_error_handler(_: Request, exc: GovernanceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


for router in routers:
    app.include_router(router)
