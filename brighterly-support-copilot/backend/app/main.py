"""FastAPI app: serves the static frontend and the API from one origin (the API key never reaches the browser)."""
from __future__ import annotations
import logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from .config import ROOT_DIR, settings
from . import db
from .api import actions, admin, chat, session

settings.validate()
db.init()
log = logging.getLogger("brighterly")
app = FastAPI(title="Brighterly Support Copilot", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(session.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(admin.router, prefix="/api")

CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith(("/js/", "/css/")) or request.url.path == "/":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.exception_handler(RequestValidationError)
async def _validation(_: Request, exc: RequestValidationError):
    return JSONResponse({"error": {"code": "invalid_request", "message": "The request was not valid."}}, status_code=422)


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception):
    log.exception("unhandled error")          # stack trace stays in the server log
    return JSONResponse({"error": {"code": "internal_error", "message": "Something went wrong."}}, status_code=500)


@app.exception_handler(429)
@app.exception_handler(401)
@app.exception_handler(403)
@app.exception_handler(404)
@app.exception_handler(409)
async def _http(_: Request, exc):
    return JSONResponse({"error": {"code": getattr(exc, "detail", "error"), "message": str(getattr(exc, "detail", "error")).replace("_", " ")}}, status_code=exc.status_code)


app.mount("/", StaticFiles(directory=str(ROOT_DIR / "frontend"), html=True), name="frontend")
