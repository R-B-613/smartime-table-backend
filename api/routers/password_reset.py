"""
api/routers/password_reset.py

The self-service "forgot password" flow, built to the Jira description.
Three endpoints, all PUBLIC (the user is logged out - they forgot their
password):

  POST /auth/forgot-password    body: {email}
       If the email is blank or not in the system -> error.
       Otherwise: make a 6-digit code, email it, return success.

  POST /auth/verify-reset-code  body: {email, code}
       If the code matches and is still valid -> success.
       Otherwise -> error.

  POST /auth/set-new-password   body: {email, code, password, confirm_password}
       Re-checks the code, checks the two passwords match and meet the
       rules (8+ chars, upper, lower, number, special), then updates the
       password and uses up the code.

Codes are kept in the password_reset_codes table and expire after 15
minutes. In test mode the code is printed to the server console instead
of being emailed (see api/emailer.py).
"""

import re
import secrets
import datetime as dt

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.security import hash_password
from api.emailer import send_reset_code
from api.auth_db import update_teacher_password
from api.reset_db import (
    get_teacher_by_email,
    create_reset_code,
    find_valid_code,
    mark_code_used,
)


router = APIRouter(prefix="/auth", tags=["password reset"])

CODE_TTL_MINUTES = 15


# ---- request bodies ----

class ForgotPasswordRequest(BaseModel):
    email: str


class VerifyCodeRequest(BaseModel):
    email: str
    code: str


class SetNewPasswordRequest(BaseModel):
    email: str
    code: str
    password: str
    confirm_password: str


class MessageResponse(BaseModel):
    detail: str


# ---- password rule check (matches the Jira restriction) ----

def _password_problem(password: str):
    if len(password) < 8:
        return "Password must be at least 8 characters long"
    if not re.search(r"[A-Z]", password):
        return "Password must include an uppercase letter"
    if not re.search(r"[a-z]", password):
        return "Password must include a lowercase letter"
    if not re.search(r"[0-9]", password):
        return "Password must include a number"
    if not re.search(r"[^A-Za-z0-9]", password):
        return "Password must include a special character"
    return None


# ---- endpoints ----

@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(body: ForgotPasswordRequest):
    email = body.email.strip()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please enter an email address",
        )

    teacher = get_teacher_by_email(email)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This email address is not registered in the system",
        )

    code = f"{secrets.randbelow(1000000):06d}"
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
        minutes=CODE_TTL_MINUTES
    )
    create_reset_code(teacher["id"], code, expires_at)
    try:
        send_reset_code(email, code)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not send the email. Check the email (SMTP) settings.",
        )

    return MessageResponse(detail="A verification code has been sent to your email")


@router.post("/verify-reset-code", response_model=MessageResponse)
def verify_reset_code(body: VerifyCodeRequest):
    teacher = get_teacher_by_email(body.email.strip())
    if teacher is None or find_valid_code(teacher["id"], body.code.strip()) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The code is incorrect or has expired",
        )
    return MessageResponse(detail="Code verified")


@router.post("/set-new-password", response_model=MessageResponse)
def set_new_password(body: SetNewPasswordRequest):
    teacher = get_teacher_by_email(body.email.strip())
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The code is incorrect or has expired",
        )

    code_row = find_valid_code(teacher["id"], body.code.strip())
    if code_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The code is incorrect or has expired",
        )

    if body.password != body.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The two passwords do not match",
        )

    problem = _password_problem(body.password)
    if problem:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=problem,
        )

    update_teacher_password(teacher["id"], hash_password(body.password))
    mark_code_used(code_row["id"])

    return MessageResponse(detail="Your password has been updated")
