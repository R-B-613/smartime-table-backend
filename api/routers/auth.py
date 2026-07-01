"""
api/routers/auth.py

Login + password operations. Three endpoints:

  POST /auth/login            public      -> issue a token
  POST /auth/change-password  any teacher -> change your OWN password
  POST /auth/reset-password   admin only  -> set another teacher's password

--- ON PASSWORD RESET AND EMAIL ---------------------------------------
There is intentionally NO email-based self-service "forgot password"
flow. The project context summary lists email/SMTP as out of scope and
states passwords are admin-provisioned. So "reset" here means an admin
sets a new password for a teacher; the teacher can then change it. If the
SMTP decision is ever reversed, the seam to add is a token-emailing
endpoint here - nothing else in this layer needs to change.

(Note: this contradicts an older note about email verification codes for
reset. I followed the context summary, which is your maintained source of
truth and explicitly says no SMTP. Flagging it rather than picking
silently - if email reset is actually required, that's a scope change.)
-----------------------------------------------------------------------
"""

from fastapi import APIRouter, Depends, HTTPException, status

from api.schemas import (
    LoginRequest,
    TokenResponse,
    TeacherProfile,
    ChangePasswordRequest,
    AdminResetPasswordRequest,
    MessageResponse,
)
from api.security import verify_password, hash_password, create_access_token
from api.deps import get_current_teacher, get_current_admin
from api.auth_db import get_teacher_for_login, get_teacher_by_id, update_teacher_password


router = APIRouter(prefix="/auth", tags=["auth"])


def _profile(teacher: dict) -> TeacherProfile:
    return TeacherProfile(
        id=teacher["id"],
        first_name=teacher.get("first_name"),
        last_name=teacher.get("last_name"),
        is_admin=bool(teacher.get("is_admin")),
        email=teacher.get("email"),
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    teacher = get_teacher_for_login(body.username)

    # One generic message whether the user is unknown, has no password set,
    # or the password is wrong - so the endpoint doesn't reveal which.
    if (
        teacher is None
        or not teacher.get("password_hash")
        or not verify_password(body.password, teacher["password_hash"])
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = create_access_token(teacher["id"], bool(teacher.get("is_admin")))
    return TokenResponse(access_token=token, teacher=_profile(teacher))


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    body: ChangePasswordRequest,
    teacher: dict = Depends(get_current_teacher),
):
    # get_current_teacher already loaded the row including password_hash.
    if not teacher.get("password_hash") or not verify_password(
        body.current_password, teacher["password_hash"]
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    if body.new_password == body.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current one",
        )

    update_teacher_password(teacher["id"], hash_password(body.new_password))
    return MessageResponse(detail="Password updated")


@router.post("/reset-password", response_model=MessageResponse)
def admin_reset_password(
    body: AdminResetPasswordRequest,
    admin: dict = Depends(get_current_admin),
):
    target = get_teacher_by_id(body.teacher_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher not found",
        )

    update_teacher_password(body.teacher_id, hash_password(body.new_password))
    return MessageResponse(
        detail=f"Password reset for teacher {body.teacher_id}"
    )
