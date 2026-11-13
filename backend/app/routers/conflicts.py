from fastapi import APIRouter

from app.services import project_service as svc

router = APIRouter(tags=["conflicts"])


@router.get("/conflicts")
def list_conflicts(stream_id: int | None = None, batch_id: int | None = None):
    return svc.list_conflicts(stream_id=stream_id, batch_id=batch_id)
