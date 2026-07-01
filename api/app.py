"""
api/app.py

The FastAPI application. Start it from the REPO ROOT:

    uvicorn api.app:app --reload

Required environment variable:
    SMARTIME_JWT_SECRET   long random string used to sign tokens

Optional environment variables:
    SMARTIME_TOKEN_EXPIRE_MINUTES   token lifetime (default 720 = 12h)
    SMARTIME_CORS_ORIGINS           comma-separated allowed origins
                                    (default "*"; set to the frontend's
                                    origin in production)
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import auth, generation, password_reset


app = FastAPI(title="SmarTime API", version="0.1.0")


# CORS: the frontend is a separate app on a different origin, so it needs
# to be allowed to call this API from the browser.
#
# We use Bearer tokens (not cookies), so allow_credentials stays False -
# which is what lets the "*" default be legal. The moment you switch to
# cookie auth you must replace "*" with explicit origins AND set
# allow_credentials=True, because the spec forbids "*" + credentials.
_origins = [
    o.strip()
    for o in os.environ.get("SMARTIME_CORS_ORIGINS", "*").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def health():
    """Liveness probe - no auth, no DB. Useful to confirm the server is up."""
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(generation.router)
app.include_router(password_reset.router)
