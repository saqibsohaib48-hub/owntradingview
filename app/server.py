"""FastAPI server: serves the web page and forwards /api/* calls to app.api_core.

Run:  python run.py        (then open http://127.0.0.1:8000)
The same page also works without this server on GitHub Pages (see app/browser.py).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import api_core

ROOT = Path(__file__).resolve().parents[1]

app = FastAPI(title="OpenTrade", version="1.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    try:
        return api_core.upload(file.filename, await file.read())
    except api_core.ApiError as e:
        raise HTTPException(e.status, e.message)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def api(rest: str, request: Request):
    body = None
    if request.method in ("POST", "PUT"):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be JSON")
    try:
        return api_core.handle(request.method, "/api/" + rest, dict(request.query_params), body)
    except api_core.ApiError as e:
        raise HTTPException(e.status, e.message)
