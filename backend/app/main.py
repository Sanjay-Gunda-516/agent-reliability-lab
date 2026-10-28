from fastapi import FastAPI

from app.api import health

app = FastAPI(title="Agent Reliability Lab")
app.include_router(health.router)
