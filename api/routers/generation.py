"""
api/routers/generation.py

Trigger a timetable generation run and poll its status.

  POST /generation/run        admin only  -> start a run, get a job_id (202)
  GET  /generation/{job_id}   any teacher -> poll status/result

Authorization choice: triggering is admin-only (it's heavy and writes new
schedule_runs - this matches the principal-driven generation workflow).
Polling is allowed for any authenticated teacher, since seeing "a new
timetable is being generated" is harmless. Tighten the GET to admin-only
by swapping get_current_teacher for get_current_admin if you prefer.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from api.schemas import GenerationStartedResponse, GenerationStatusResponse
from api.deps import get_current_admin, get_current_teacher
from api.jobs import start_generation_job, get_job


router = APIRouter(prefix="/generation", tags=["generation"])


@router.post(
    "/run",
    response_model=GenerationStartedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_generation(admin: dict = Depends(get_current_admin)):
    job_id = start_generation_job()
    if job_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A generation run is already in progress",
        )
    return GenerationStartedResponse(job_id=job_id, status="running")


@router.get("/{job_id}", response_model=GenerationStatusResponse)
def generation_status(
    job_id: str,
    teacher: dict = Depends(get_current_teacher),
):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found (it may have been lost on a server restart)",
        )
    return GenerationStatusResponse(**job)
