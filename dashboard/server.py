"""FastAPI app for the Grover streaming dashboard.

Serves the single-page dashboard and a ``/ws`` WebSocket. The recorder runs as
a background task in the app lifespan; each poll it produces a JSON tick that is
fanned out to every connected browser. New clients get a full snapshot on
connect so charts and panels populate immediately.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard.feeds import run_feed
from dashboard.recorder import Recorder

STATIC = Path(__file__).resolve().parent / "static"


class Hub:
    """Tracks connected websockets and broadcasts messages to all of them."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.add(ws)

    async def drop(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":"))
        async with self._lock:
            targets = list(self.clients)
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 - prune dead sockets
                await self.drop(ws)


hub = Hub()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    recorder = Recorder(on_message=hub.broadcast)
    app.state.recorder = recorder
    task = asyncio.create_task(recorder.run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Grover Streaming Dashboard", lifespan=lifespan)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
async def config() -> dict[str, Any]:
    return app.state.recorder.config()


@app.get("/api/feed/{feed_id}")
async def feed(feed_id: str, request: Request) -> dict[str, Any]:
    recorder: Recorder = app.state.recorder
    if recorder.api is None or not recorder.has_key:
        return {"ok": False, "id": feed_id, "error": "MOONDEV_API_KEY not set — feeds are offline."}
    params = dict(request.query_params)
    return await asyncio.to_thread(run_feed, recorder.api, feed_id, params)


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    recorder: Recorder = websocket.app.state.recorder
    await hub.add(websocket)
    try:
        await websocket.send_text(json.dumps(recorder.snapshot(), separators=(",", ":")))
        while True:  # keep the socket open; we don't need client messages yet
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        await hub.drop(websocket)


app.mount("/", StaticFiles(directory=str(STATIC)), name="static")
