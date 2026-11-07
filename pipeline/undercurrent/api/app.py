"""Thin API over the exported JSON.

Run: npm run dev:api   (or: uv run uvicorn undercurrent.api.app:app --reload --port 8000)

Serves data/export/ once the pipeline has written meta.json there, otherwise web/public/mock/,
so the web app works from minute one. Responses are the files as-is: their shapes are defined
in contracts.py.
"""

import datetime as dt
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.responses import FileResponse

from undercurrent.config import EXPORT, MOCK
from undercurrent.contracts import STORY_ID_PATTERN

app = FastAPI(title="Undercurrent API")


def _root() -> Path:
    return EXPORT if (EXPORT / "meta.json").exists() else MOCK


def _file(rel: str) -> FileResponse:
    path = _root() / rel
    if not path.is_file():
        raise HTTPException(404, f"{rel} not found in {_root().name}/")
    return FileResponse(path, media_type="application/json")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "serving": _root().name}


@app.get("/api/meta")
def meta() -> FileResponse:
    return _file("meta.json")


@app.get("/api/snapshots/{day}")
def snapshot(day: dt.date) -> FileResponse:
    return _file(f"snapshots/{day.isoformat()}.json")


@app.get("/api/stories/{story_id}")
def story(story_id: str = PathParam(pattern=STORY_ID_PATTERN)) -> FileResponse:
    return _file(f"stories/{story_id}.json")


# TODO(Workstream D + A): @app.websocket("/api/live") streaming contracts.LivePost messages
# from the Bluesky Jetstream consumer.
