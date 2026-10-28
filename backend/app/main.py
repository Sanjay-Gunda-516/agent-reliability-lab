from fastapi import FastAPI

from app.api import agent, health, test_runs

app = FastAPI(title="Agent Reliability Lab")
app.include_router(health.router)
app.include_router(agent.router)
app.include_router(test_runs.router)
