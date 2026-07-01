"""
api/routers/schedule.py

Read-only endpoints that return the CURRENT timetable (the latest run
marked is_selected = true).

  GET /schedule/current   admin only   -> every lesson in the school
  GET /schedule/me        any teacher  -> only the logged-in teacher's lessons

Both return the same shape:
  {
    "run": { "id", "algorithm", "score", "run_at" } | null,
    "entries": [
      { "day_of_week", "hour_of_day", "teacher_id",
        "teacher_first_name", "teacher_last_name",
        "subject_name", "group_name", "room_name" },
      ...
    ]
  }

If no schedule has been generated yet, "run" is null and "entries" is [].
"""

from fastapi import APIRouter, Depends

from api.deps import get_current_teacher, get_current_admin
from api.schedule_db import get_current_run, get_schedule_entries


router = APIRouter(prefix="/schedule", tags=["schedule"])


def _payload(run, teacher_id=None):
    if run is None:
        return {"run": None, "entries": []}
    entries = get_schedule_entries(run["id"], teacher_id=teacher_id)
    return {"run": run, "entries": entries}


@router.get("/current")
def current_schedule(admin: dict = Depends(get_current_admin)):
    """Whole-school timetable. Admin only."""
    return _payload(get_current_run())


@router.get("/me")
def my_schedule(teacher: dict = Depends(get_current_teacher)):
    """Just the logged-in teacher's lessons."""
    return _payload(get_current_run(), teacher_id=teacher["id"])
