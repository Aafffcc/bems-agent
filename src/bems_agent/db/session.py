from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from bems_agent.core.config import get_settings

settings = get_settings()
engine: AsyncEngine = create_async_engine(
    settings.postgres_dsn,
    echo=settings.app_debug,
    pool_pre_ping=True,
)


async def init_database() -> None:
    try:
        async with engine.begin():
            return
    except Exception:
        return


async def close_database() -> None:
    await engine.dispose()


async def check_database_connection() -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
