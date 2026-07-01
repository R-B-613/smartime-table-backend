"""
api/security.py

Two unrelated security concerns kept in one small place:

  1. Password hashing  (bcrypt)  -> hash_password / verify_password
  2. Stateless auth tokens (JWT) -> create_access_token / decode_access_token

Both the hashing scheme and the token scheme are wrapped behind these
functions ON PURPOSE, so if you ever want to swap bcrypt for argon2, or
JWT for server-side sessions, you change it here and nowhere else.

--- DECISIONS YOU MAY WANT TO REVISIT ---------------------------------
* Hashing = bcrypt (via the `bcrypt` package directly, not passlib, to
  avoid the passlib/bcrypt-4.x version friction). The SAME hash_password
  function MUST be used wherever passwords are first set (see
  api/provision.py) - otherwise login can never match.
* Auth = JWT (stateless). Chosen because there is NO sessions table in
  the schema and you asked for minimal. The trade-off: you cannot
  invalidate a single token before it expires (no server-side record of
  issued tokens). If you need real logout/revocation, switch to a
  sessions table instead - that's a bigger change and would mean a new
  table, which the "fixed, not dynamic" schema discussion didn't plan.
* The signing secret is read from the env var SMARTIME_JWT_SECRET and is
  NEVER hardcoded. If it's missing, token operations fail loudly rather
  than silently using a default (which would be a security hole).
-----------------------------------------------------------------------
"""

import os
import datetime as dt

import bcrypt
import jwt  # PyJWT


JWT_ALGORITHM = "HS256"

# Token lifetime. 12 hours by default; override with the env var.
ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.environ.get("SMARTIME_TOKEN_EXPIRE_MINUTES", "720")
)


def _get_secret() -> str:
    secret = os.environ.get("SMARTIME_JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "SMARTIME_JWT_SECRET environment variable is not set. "
            "Set it to a long random string before starting the server."
        )
    return secret


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """
    Returns a bcrypt hash (as a str, ready to store in teachers.password_hash).

    NOTE: bcrypt only uses the first 72 BYTES of the password. That's fine
    for normal passwords; just be aware extremely long inputs are truncated.
    """
    hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """
    Constant-time check of a plaintext password against a stored bcrypt hash.
    Returns False (rather than raising) if the stored value is empty or isn't
    a valid bcrypt hash, so callers can treat "no password set" as "login
    fails" without special-casing.
    """
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT access tokens
# ---------------------------------------------------------------------------

def create_access_token(teacher_id: int, is_admin: bool) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(teacher_id),
        "is_admin": bool(is_admin),
        "iat": now,
        "exp": now + dt.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, _get_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Returns the decoded payload, or raises a jwt.PyJWTError subclass
    (ExpiredSignatureError, InvalidTokenError, ...) that the auth
    dependency turns into a 401.
    """
    return jwt.decode(token, _get_secret(), algorithms=[JWT_ALGORITHM])
