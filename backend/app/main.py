from fastapi import FastAPI

from app.api import agent, health

app = FastAPI(title="Agent Reliability Lab")
app.include_router(health.router)
app.include_router(agent.router)
