from fastapi import APIRouter

from app.api.v1.clusters import router as clusters_router
from app.api.v1.events import router as events_router
from app.api.v1.logs import router as logs_router
from app.api.v1.personas import router as personas_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(personas_router)
api_router.include_router(events_router)
api_router.include_router(clusters_router)
api_router.include_router(logs_router)
