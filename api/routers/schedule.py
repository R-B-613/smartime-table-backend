"""
api/routers/schedule.py
Schedule views + the admin "publish to staff" action.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import get_current_teacher, get_current_admin
from api.schedule_db import (
    get_current_run,
    get_published_run,
    get_schedule_entries,
    publish_run,
)

router = APIRouter(prefix="/schedule", tags=["schedule"])


def _payload(run, teacher_id=None):
    if run is None:
        return {"run": None, "entries": []}
    entries = get_schedule_entries(run["id"], teacher_id=teacher_id)
    return {"run": run, "entries": entries}


@router.get("/current")
def current_schedule(admin: dict = Depends(get_current_admin)):
    return _payload(get_current_run())


@router.get("/me")
def my_schedule(teacher: dict = Depends(get_current_teacher)):
    return _payload(get_published_run(), teacher_id=teacher["id"])


@router.post("/publish")
def publish_current(admin: dict = Depends(get_current_admin)):
    run = get_current_run()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="There is no schedule to publish yet",
        )
    publish_run(run["id"])
    return {"detail": "published", "run_id": run["id"]}
