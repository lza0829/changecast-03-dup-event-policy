from fastapi import APIRouter

from app.routers import (
    batches,
    buffer,
    conflicts,
    dashboard,
    events,
    projections,
    settings,
    stream_policy,
    streams,
)

api = APIRouter(prefix="/api")
api.include_router(dashboard.router)
api.include_router(streams.router)
api.include_router(stream_policy.router)
api.include_router(batches.router)
api.include_router(projections.router)
api.include_router(events.router)
api.include_router(buffer.router)
api.include_router(conflicts.router)
api.include_router(settings.router)
