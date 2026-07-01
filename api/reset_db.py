"""
api/reset_db.py  (in-memory codes version)

Looks up the teacher by email in the DATABASE, but keeps the temporary
6-digit reset CODES in MEMORY rather than in a table.

Why: a reset code is a throwaway value that only needs to survive ~15
minutes, between the moment it's emailed and the moment the user types it
back. So there's no reason to store it permanently - this avoids creating
an extra database table.

Trade-off (fine for this project): if the server is restarted while
someone is mid-reset, their code is forgotten and they just request a new
one. Assumes a single server process - the same assumption the generation
jobs already make.

Passwords themselves are NOT here - they live in teachers.password_hash,
read/written via api/auth_db.py.
"""

import threading
import datetime as dt

from psycopg2.extras import RealDictCursor

from data_access import get_db_connection


# teacher_id -> {"code": str, "expires_at": datetime, "used": bool}
_CODES = {}
_LOCK = threading.Lock()


def get_teacher_by_email(email: str):
    """Returns {id, email} for the teacher with this email, or None. (DB lookup.)"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT id, email FROM teachers WHERE email = %s;",
                (email,),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def create_reset_code(teacher_id, code, expires_at):
    """Stores (in memory) the latest code for a teacher, replacing any older one."""
    with _LOCK:
        _CODES[teacher_id] = {"code": code, "expires_at": expires_at, "used": False}


def find_valid_code(teacher_id, code):
    """
    Returns {"id": teacher_id} if the teacher's stored code matches and is
    still unused and unexpired; otherwise None.
    """
    now = dt.datetime.now(dt.timezone.utc)
    with _LOCK:
        entry = _CODES.get(teacher_id)
        if (
            entry is not None
            and not entry["used"]
            and entry["expires_at"] > now
            and entry["code"] == code
        ):
            return {"id": teacher_id, "teacher_id": teacher_id}
    return None


def mark_code_used(code_id):
    """Marks the teacher's code used so it can't be reused (code_id == teacher_id here)."""
    with _LOCK:
        entry = _CODES.get(code_id)
        if entry is not None:
            entry["used"] = True
