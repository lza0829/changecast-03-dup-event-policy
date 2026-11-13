from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engines.project import VALID_CONFLICT_OUTCOMES, VALID_POLICIES
from app.services import project_service as svc

router = APIRouter(tags=["streams"])


class PolicyBody(BaseModel):
    duplicate_policy: str
    conflict_outcome: str


@router.get("/streams/{stream_id}/policy")
def get_stream_policy(stream_id: int):
    streams = {s["id"]: s for s in svc.list_streams()}
    stream = streams.get(stream_id)
    if not stream:
        raise HTTPException(404, "stream not found")
    return {
        "stream_id": stream_id,
        "duplicate_policy": stream["duplicate_policy"],
        "conflict_outcome": stream["conflict_outcome"],
        "valid_policies": list(VALID_POLICIES),
        "valid_conflict_outcomes": list(VALID_CONFLICT_OUTCOMES),
    }


@router.put("/streams/{stream_id}/policy")
def update_stream_policy(stream_id: int, body: PolicyBody):
    try:
        return svc.update_stream_policy(
            stream_id, body.duplicate_policy, body.conflict_outcome
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
