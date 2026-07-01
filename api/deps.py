"""
api/deps.py

FastAPI dependencies that turn a Bearer token into a teacher record, and
gate admin-only routes.

Uses HTTPBearer (Authorization: Bearer <token>) rather than the OAuth2
password-form flow, because the token is issued by our own /auth/login
and the frontend just stores and replays it.
"""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from api.security import decode_access_token
from api.auth_db import get_teacher_by_id


bearer_scheme = HTTPBearer(auto_error=True)


def get_current_teacher(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    Validates the token and loads the teacher fresh from the DB on every
    request (so a deleted/edited teacher is reflected immediately, and so
    is_admin always comes from the database, not just the token claim).
    Returns the teacher dict (including password_hash, which change-password
    needs).
    """
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    try:
        teacher_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token",
        )

    teacher = get_teacher_by_id(teacher_id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account no longer exists",
        )
    return teacher


def get_current_admin(teacher: dict = Depends(get_current_teacher)) -> dict:
    """
    Same as get_current_teacher, but rejects non-admins with 403.
    Used to gate generation triggering and admin password resets.
    """
    if not teacher.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return teacher
