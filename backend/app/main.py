from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agent.checkpoints import close_checkpointer
from app.api import agent, approvals, health, test_runs, traces
from app.observability.logging import configure_logging
from app.observability.middleware import request_context


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    yield
    close_checkpointer()


app = FastAPI(title="Agent Reliability Lab", lifespan=lifespan)
app.middleware("http")(request_context)
app.include_router(health.router)
app.include_router(agent.router)
app.include_router(approvals.router)
app.include_router(test_runs.router)
app.include_router(traces.router)
