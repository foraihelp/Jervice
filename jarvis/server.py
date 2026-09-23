"""Local HTTP API so other devices (the iOS app, or anything else on your
Tailscale network) can talk to the same Jarvis brain that runs on this PC
-- including the PC-control tools (open_app, focus_window, etc.), since
those only work when executed on this machine.

Security model: this is a single-user personal assistant, not a multi-tenant
service. Auth is a single shared bearer token (JARVIS_API_TOKEN in .env).
Do NOT port-forward this server directly onto the public internet -- use
Tailscale (or another private VPN/mesh) so only your own devices can reach
it. See README.md for setup.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("jarvis.server")


class ChatRequest(BaseModel):
    text: str


class ChatResponse(BaseModel):
    reply: str


def create_app(brain_holder: Any, api_token: str) -> FastAPI:
    """`brain_holder` is a jarvis.brain.BrainHolder -- read via `.brain` on
    every request (not captured once) so a live provider switch from the
    Settings window (which swaps `brain_holder.brain`) also applies to
    remote (iOS) requests without restarting the server."""
    app = FastAPI(title="Jarvis Local API", version="0.1.0")

    def _check_auth(authorization: str | None) -> None:
        expected = f"Bearer {api_token}"
        if not authorization or authorization != expected:
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
        _check_auth(authorization)
        if not req.text.strip():
            raise HTTPException(status_code=400, detail="Empty 'text' field.")

        logger.info("Remote chat request: %r", req.text)
        try:
            reply = brain_holder.brain.respond(req.text)
        except Exception as exc:  # noqa: BLE001 - never 500 without a clean message
            logger.exception("Brain error handling remote chat request")
            raise HTTPException(status_code=500, detail=f"Brain error: {exc}") from exc

        return ChatResponse(reply=reply)

    return app


def run_server(brain_holder: Any, api_token: str, host: str, port: int) -> None:
    """Blocks the calling thread running the API server. Call this from a
    background thread, not the main thread (main thread runs the tray app)."""
    import uvicorn

    app = create_app(brain_holder, api_token)
    logger.info("Starting local API server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")
