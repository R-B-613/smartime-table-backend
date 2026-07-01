# SmarTime API layer — reference

This `api/` folder is a small web server that sits on top of your existing
backend. Your original files (main.py, the solvers, data_access.py, etc.)
are **not changed** by any of this — the api folder only *uses* them.

It exists because a website can't do four things by talking straight to
the database: **log in**, **change/reset a password**, **start a timetable
generation**, and **check whether that generation is finished**. Those
four things need a server. Everything else (teacher lists, preferences,
viewing schedules) your friend's frontend still does straight against the
database.

---

## Folder layout

Put the `api/` folder at the **repo root** — the same folder that already
contains `data_access.py`, `comparator.py`, `csp/`, `hill_climbing/`,
`genetic/`:

```
smartime-table-backend/        <- repo root (already exists)
├── main.py                    (your original, unchanged)
├── data_access.py             (your original, unchanged)
├── comparator.py              (your original, unchanged)
├── performance.py             (your original, unchanged)
├── scoring_config.py          (your original, unchanged)
├── view_schedule.py           (your original, unchanged)
├── csp/solver.py              (your original, unchanged)
├── hill_climbing/solver.py    (your original, unchanged)
├── genetic/solver.py          (your original, unchanged)
│
└── api/                       <- NEW folder, everything below is new
    ├── __init__.py
    ├── app.py
    ├── security.py
    ├── auth_db.py
    ├── reset_db.py
    ├── emailer.py
    ├── schemas.py
    ├── deps.py
    ├── jobs.py
    ├── provision.py
    ├── requirements-api.txt
    ├── start_api.sh
    ├── README.md              (this file)
    └── routers/
        ├── __init__.py
        ├── auth.py
        ├── generation.py
        └── password_reset.py
```

---

## What each file does

**app.py** — the server itself. This is the file you start. It switches on
the three groups of endpoints (auth, generation, password reset) and a
`/health` check. Connects to: the three router files.

**security.py** — two tools: scrambling passwords (bcrypt) and making login
tokens (JWT). Every other file that needs to hash or check a password, or
make a token, calls this one. Reads the secret `SMARTIME_JWT_SECRET`.

**auth_db.py** — the database questions for login: find a teacher by their
ID, find one by primary key, update a password. The line
`LOGIN_COLUMN = "teacher_identity"` is what makes login use the teacher ID.
Connects to: your `data_access.get_db_connection()` (so DB connection logic
stays in one place).

**reset_db.py** — handles the forgot-password flow. It finds a teacher by
email in the database, but keeps the temporary reset CODES in memory (not
in a table), since a code only needs to live ~15 minutes. Passwords are
NOT here — they live in `teachers.password_hash`. Connects to:
`data_access.get_db_connection()` (for the email lookup only).

**emailer.py** — sends the reset code by email. If email isn't configured it
falls back to **printing the code in the terminal** (test mode). Reads the
`SMARTIME_SMTP_*` settings.

**schemas.py** — describes the shape of each request and response (e.g.
"login needs a username and a password"). Used by the auth and generation
routers so FastAPI can validate input automatically.

**deps.py** — the gatekeeper. On a protected request it reads the login
token, loads that teacher from the DB, and (for admin-only routes) checks
they're an admin. Connects to: security.py and auth_db.py.

**jobs.py** — runs the actual timetable generation in the background (the
same fetch → 3 algorithms → save-best sequence as main.py), and remembers
whether each run is still going / done / failed so the website can poll.
Connects to: your `data_access`, `performance`, `comparator`, and the three
solvers.

**provision.py** — a small command (not a web endpoint) to set a teacher's
first password, hashed the same way login expects. Run:
`python -m api.provision <teacher_id> '<password>'`.

**routers/auth.py** — the endpoints `POST /auth/login`,
`POST /auth/change-password`, and an admin `POST /auth/reset-password`.

**routers/generation.py** — `POST /generation/run` (admin only; starts a
run, returns a job id) and `GET /generation/{job_id}` (check status).

**routers/password_reset.py** — the forgot-password flow from Jira:
`POST /auth/forgot-password`, `POST /auth/verify-reset-code`,
`POST /auth/set-new-password`.

**requirements-api.txt** — the extra Python packages this layer needs.

**start_api.sh** — a fill-in-the-blanks script that sets the settings and
starts the server.

---

## The endpoints (the full list)

| Method | Path                      | Who          | What it does                          |
|--------|---------------------------|--------------|----------------------------------------|
| GET    | /health                   | anyone       | "is the server up?"                    |
| POST   | /auth/login               | anyone       | log in, get a token                    |
| POST   | /auth/change-password     | logged in    | change your own password               |
| POST   | /auth/reset-password      | admin        | admin sets another teacher's password  |
| POST   | /auth/forgot-password     | anyone       | email me a reset code                  |
| POST   | /auth/verify-reset-code   | anyone       | check the code is right                |
| POST   | /auth/set-new-password    | anyone       | set a new password using the code      |
| POST   | /generation/run           | admin        | start generating a timetable           |
| GET    | /generation/{job_id}      | logged in    | is the generation done?                |

---

## One-time setup

1. **Install the extra packages** (with your virtual env on):
   ```
   pip install -r api/requirements-api.txt
   ```

2. **Fill in start_api.sh** with a JWT secret and your email settings
   (see the email section below).

3. **Give one admin a password** so you can log in. Pick a teacher whose
   `is_admin` is true (check in DBeaver), note their `id`, then:
   ```
   python -m api.provision 21 'SomeStrong!Pass1'
   ```

4. **Start the server** from the repo root:
   ```
   source ~/scheduler_env/bin/activate
   bash api/start_api.sh
   ```
   Then open http://localhost:8001/docs — a clickable page to try every
   endpoint.

> No database table is needed for password reset — the codes are kept in
> memory (see reset_db.py).

> Note: this runs on port **8001** so it doesn't collide with the old
> service already running on 8000.

---

## Real email (Gmail example)

Gmail won't accept your normal password from a script. You need a
one-time "app password":

1. The Gmail account must have 2-Step Verification turned on.
2. Go to the Google Account → Security → App passwords.
3. Create one (name it "SmarTime"). Google shows a 16-character password.
4. Put it in start_api.sh as `SMARTIME_SMTP_PASSWORD`, and your Gmail
   address as both `SMARTIME_SMTP_USER` and `SMARTIME_SMTP_FROM`.

If you leave the `SMARTIME_SMTP_*` blanks empty, the system stays in **test
mode** and prints the code in the terminal instead — so the flow always
works, even before email is set up.

A school/institution SMTP server works the same way: put its host, port,
username and password in those four settings.
