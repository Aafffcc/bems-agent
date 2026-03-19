from fastapi import APIRouter

from bems_agent.core.config import get_settings
from bems_agent.db.session import check_database_connection

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, object]:
    settings = get_settings()
    database_ok = await check_database_connection()

    return {
        "status": "ok" if database_ok else "degraded",
        "service": settings.app_name,
        "environment": settings.app_env,
        "database": "connected" if database_ok else "disconnected",
    }
