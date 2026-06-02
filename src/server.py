"""
FastAPI server exposing OpenAI-compatible endpoints.

Endpoints:
- GET  /v1/models              — list available models (fetched dynamically from Copilot)
- POST /v1/chat/completions    — chat completions (streaming)
- GET  /auth/status            — check auth status
- POST /auth/login             — trigger device flow login
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from .auth import Credentials, load_credentials, login
from .models import fetch_models, to_openai_model_object
from .proxy import proxy_chat_completion


# ─── Global state ────────────────────────────────────────────────────────────

_credentials: Optional[Credentials] = None
_login_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load saved credentials on startup."""
    global _credentials
    creds = load_credentials()
    if creds is not None:
        _credentials = creds
        print("[copilot-proxy] Loaded saved credentials.")
    else:
        print("[copilot-proxy] No saved credentials. Run POST /auth/login to authenticate.")
    yield


app = FastAPI(
    title="Copilot Proxy",
    description="OpenAI-compatible proxy for GitHub Copilot API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_auth() -> Credentials:
    """Raise 401 if not authenticated."""
    if _credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. POST /auth/login to start device flow.",
        )
    return _credentials


# ─── Auth endpoints ──────────────────────────────────────────────────────────


@app.get("/auth/status")
async def auth_status():
    if _credentials is None:
        return {"authenticated": False}
    return {"authenticated": True}


@app.post("/auth/login")
async def auth_login():
    """Start the device flow login. Returns the user code and verification URI."""
    global _credentials, _login_task

    from .auth import start_device_flow, poll_for_token, _save_credentials

    device = await start_device_flow()

    async def _do_login():
        global _credentials
        oauth_token = await poll_for_token(
            device["device_code"], device["interval"], device["expires_in"]
        )
        _credentials = Credentials(oauth_token=oauth_token)
        _save_credentials(_credentials)
        print("[copilot-proxy] Authenticated!")

    _login_task = asyncio.create_task(_do_login())

    return {
        "verification_uri": device["verification_uri"],
        "user_code": device["user_code"],
        "expires_in": device["expires_in"],
        "message": f"Open {device['verification_uri']} and enter code: {device['user_code']}",
    }


# ─── Models endpoint ─────────────────────────────────────────────────────────


@app.get("/v1/models")
async def get_models():
    """List available models — fetched dynamically from Copilot API."""
    creds = _require_auth()
    try:
        models = await fetch_models(creds)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch models: {e}")

    return {
        "object": "list",
        "data": [to_openai_model_object(m) for m in models],
    }


# ─── Chat Completions endpoint ───────────────────────────────────────────────


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    OpenAI-compatible chat completions endpoint.
    Routes to /chat/completions or /responses based on model prefix.
    """
    creds = _require_auth()

    body = await request.json()
    model = body.get("model", "gpt-4o")
    messages = body.get("messages", [])
    stream = body.get("stream", True)
    tools = body.get("tools")
    temperature = body.get("temperature")
    max_tokens = body.get("max_tokens")

    if not messages:
        raise HTTPException(status_code=400, detail="messages is required")

    if stream:
        return StreamingResponse(
            proxy_chat_completion(
                creds, model, messages, stream=True,
                tools=tools, temperature=temperature, max_tokens=max_tokens,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        # Non-streaming: collect all chunks and return as single response
        full_content = ""
        finish_reason = "stop"
        usage = None
        tool_calls_list = []

        async for sse_line in proxy_chat_completion(
            creds, model, messages, stream=True,
            tools=tools, temperature=temperature, max_tokens=max_tokens,
        ):
            if not sse_line.startswith("data: "):
                continue
            data_str = sse_line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                if delta.get("content"):
                    full_content += delta["content"]
                if delta.get("tool_calls"):
                    tool_calls_list.extend(delta["tool_calls"])
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                if chunk.get("usage"):
                    usage = chunk["usage"]
            except json.JSONDecodeError:
                continue

        response: dict = {
            "id": f"chatcmpl-proxy-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": full_content or None,
                    },
                    "finish_reason": finish_reason,
                }
            ],
        }
        if tool_calls_list:
            response["choices"][0]["message"]["tool_calls"] = tool_calls_list
        if usage:
            response["usage"] = usage

        return JSONResponse(response)


# ─── Health check ────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "authenticated": _credentials is not None}
