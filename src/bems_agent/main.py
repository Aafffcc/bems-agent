from contextlib import asynccontextmanager

from fastapi import FastAPI

from bems_agent.agent.service import agent_runtime
from bems_agent.api.router import api_router
from bems_agent.core.config import get_settings
from bems_agent.db.session import close_database, init_database

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_database()
    yield
    await agent_runtime.shutdown()
    await close_database()


app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router, prefix=settings.api_v1_prefix)
