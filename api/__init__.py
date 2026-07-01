"""
api package

A deliberately small FastAPI layer over the existing SmarTime backend.

It exposes ONLY the four kinds of operation that genuinely need a
request/response server (and therefore can't be done by the frontend
talking straight to PostgreSQL):

    - login / auth        -> api/routers/auth.py
    - password operations -> api/routers/auth.py
    - trigger generation  -> api/routers/generation.py
    - generation status   -> api/routers/generation.py

Everything else (teacher CRUD, preferences, viewing schedules) stays as
direct DB access from the frontend, exactly as decided in the project
context summary.

Run from the REPO ROOT (the directory that contains data_access.py,
comparator.py, csp/, hill_climbing/, genetic/) with:

    uvicorn api.app:app --reload

Running from inside api/ will break the imports of the core modules.
"""
