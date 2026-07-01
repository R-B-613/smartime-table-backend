"""
api/auth_db.py

The only auth-related database queries. Kept separate from data_access.py
so the verified scheduling-data layer stays untouched, but it REUSES
data_access.get_db_connection() so there is still exactly one place that
knows how to connect (credentials.txt, host, etc.).

--- THE ONE DECISION TO CONFIRM ---------------------------------------
LOGIN_COLUMN controls which teachers column the login form's "username"
is matched against. I defaulted to "email" because it's the conventional
unique login and data_access already reads it.

BUT the teachers table also has `teacher_identity`, which in this kind of
system is often the actual ID people log in with. Whichever field your
friend's frontend login form submits is the one that has to go here.
Flip this single constant if it's teacher_identity (or change it to
whatever the form sends). It is a fixed constant, never user input, so
interpolating it into the query is safe.
-----------------------------------------------------------------------
"""

from psycopg2.extras import RealDictCursor

from data_access import get_db_connection


LOGIN_COLUMN = "teacher_identity"  # teachers log in with their ID

# Columns auth ever needs. Note password_hash is included here but is NOT
# part of fetch_all_data() (the algorithms never see it).
_AUTH_COLUMNS = "id, first_name, last_name, is_admin, email, password_hash"


def get_teacher_for_login(login_value: str):
    """
    Looks up a teacher by the configured LOGIN_COLUMN. Returns a dict
    (RealDict) including password_hash, or None if no such teacher.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                f"SELECT {_AUTH_COLUMNS} FROM teachers WHERE {LOGIN_COLUMN} = %s;",
                (login_value,),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def get_teacher_by_id(teacher_id: int):
    """
    Looks up a teacher by primary key. Used when validating a token and
    when an admin resets someone's password. Returns a dict or None.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                f"SELECT {_AUTH_COLUMNS} FROM teachers WHERE id = %s;",
                (teacher_id,),
            )
            return cursor.fetchone()
    finally:
        conn.close()


def update_teacher_password(teacher_id: int, new_password_hash: str) -> int:
    """
    Sets teachers.password_hash for one teacher. Returns the number of rows
    updated (0 means no such teacher).
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE teachers SET password_hash = %s WHERE id = %s;",
                (new_password_hash, teacher_id),
            )
            updated = cursor.rowcount
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
