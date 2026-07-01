"""
api/provision.py

Small command-line helper so initial / admin-provisioned passwords are
hashed with the SAME function login verifies against. Without this, a
password set by some other means (e.g. a hand-written hash, or plaintext)
would never match at /auth/login.

Run from the repo root:

    python -m api.provision <teacher_id> <new_password>

Example:
    python -m api.provision 1 'ChangeMe123!'

This is an admin/ops utility, not an HTTP endpoint - it talks straight to
the DB, exactly like the rest of your provisioning scripts.
"""

import sys

from api.security import hash_password
from api.auth_db import update_teacher_password, get_teacher_by_id


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("Usage: python -m api.provision <teacher_id> <new_password>")
        return 1

    try:
        teacher_id = int(argv[0])
    except ValueError:
        print("teacher_id must be an integer.")
        return 1

    new_password = argv[1]
    if len(new_password) < 8:
        print("Password must be at least 8 characters.")
        return 1

    if get_teacher_by_id(teacher_id) is None:
        print(f"No teacher with id {teacher_id}.")
        return 1

    update_teacher_password(teacher_id, hash_password(new_password))
    print(f"Password set for teacher {teacher_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
