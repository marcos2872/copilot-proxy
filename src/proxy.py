"""
Proxy logic: translates OpenAI-compatible requests to Copilot API calls.

Routing:
- gpt-5* models → POST /responses (Responses API format)
- All others    → POST /chat/completions (pass-through)

The oauth_token is used directly as Bearer in all requests.
"""

import json
import time
import uuid
from typing import AsyncIterator

import httpx

from .auth import Credentials, BASE_URL
from .models import is_responses_model


# ─── Completions Backend (pass-through) ─────────────────────────────────────


def _build_completions_payload(
    model: str,
    messages: list[dict],
    stream: bool,
    tools: list[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Build payload for /chat/completions."""
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return payload


# ─── Responses Backend ───────────────────────────────────────────────────────


def _convert_messages_to_responses_input(messages: list[dict]) -> list[dict]:
    """
    Convert OpenAI Chat Completions messages to Responses API input format.

    Critical: function_call and function_call_output are ROOT-LEVEL items
    in the input array, NOT inside content[].
    """
    result = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            text = content if isinstance(content, str) else json.dumps(content)
            result.append({"role": "system", "content": [{"type": "input_text", "text": text}]})

        elif role == "user":
            if isinstance(content, str):
                result.append({"role": "user", "content": [{"type": "input_text", "text": content}]})
            elif isinstance(content, list):
                parts = []
                for part in content:
                    if part.get("type") == "text":
                        parts.append({"type": "input_text", "text": part["text"]})
                    elif part.get("type") == "image_url":
                        parts.append({"type": "input_image", "image_url": part["image_url"]["url"]})
                    else:
                        parts.append(part)
                result.append({"role": "user", "content": parts})

        elif role == "assistant":
            if msg.get("tool_calls"):
                # Assistant text (if any) as output_text
                if content:
                    result.append(
                        {"role": "assistant", "content": [{"type": "output_text", "text": content}]}
                    )
                # Each tool_call becomes a root-level function_call item
                for tc in msg["tool_calls"]:
                    result.append({
                        "type": "function_call",
                        "call_id": tc["id"],
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    })
            else:
                text = content if isinstance(content, str) else ""
                result.append(
                    {"role": "assistant", "content": [{"type": "output_text", "text": text}]}
                )

        elif role == "tool":
            # Tool results are root-level function_call_output items
            output = content if isinstance(content, str) else json.dumps(content)
            result.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id", ""),
                "output": output,
            })

    return result


def _convert_tools_to_responses(tools: list[dict] | None) -> list[dict] | None:
    """
    Convert OpenAI tools format to Responses API format.
    OpenAI: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    Responses: {"type": "function", "name": ..., "description": ..., "parameters": ...}
    """
    if not tools:
        return None
    result = []
    for tool in tools:
        if tool.get("type") == "function":
            fn = tool["function"]
            result.append({
                "type": "function",
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            })
    return result or None


def _build_responses_payload(
    model: str,
    messages: list[dict],
    stream: bool,
    tools: list[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Build payload for /responses (GPT-5+)."""
    payload: dict = {
        "model": model,
        "input": _convert_messages_to_responses_input(messages),
        "stream": stream,
    }
    converted_tools = _convert_tools_to_responses(tools)
    if converted_tools:
        payload["tools"] = converted_tools
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_output_tokens"] = max_tokens
    return payload


# ─── SSE helpers ─────────────────────────────────────────────────────────────


def _make_completion_chunk(
    chunk_id: str,
    model: str,
    content: str | None = None,
    finish_reason: str | None = None,
    tool_calls: list | None = None,
    usage: dict | None = None,
) -> dict:
    """Create an OpenAI Chat Completions streaming chunk."""
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls

    chunk: dict = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage:
        chunk["usage"] = usage
    return chunk


# ─── Stream: /chat/completions (pass-through) ───────────────────────────────


async def _stream_completions(
    creds: Credentials,
    model: str,
    messages: list[dict],
    stream: bool,
    tools: list[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """Stream from /chat/completions — pass-through since format matches."""
    payload = _build_completions_payload(model, messages, stream, tools, temperature, max_tokens)

    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream(
            "POST",
            f"{BASE_URL}/chat/completions",
            headers=creds.headers(),
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise httpx.HTTPStatusError(
                    f"Copilot API error {resp.status_code}: {body.decode()}",
                    request=resp.request,
                    response=resp,
                )
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield line + "\n\n"
                    if line == "data: [DONE]":
                        break


# ─── Stream: /responses (GPT-5+) → convert to completions format ────────────


async def _stream_responses(
    creds: Credentials,
    model: str,
    messages: list[dict],
    stream: bool,
    tools: list[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """Stream from /responses and convert to Chat Completions SSE format."""
    payload = _build_responses_payload(model, messages, stream, tools, temperature, max_tokens)
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream(
            "POST",
            f"{BASE_URL}/responses",
            headers=creds.headers(),
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise httpx.HTTPStatusError(
                    f"Copilot API error {resp.status_code}: {body.decode()}",
                    request=resp.request,
                    response=resp,
                )

            tool_calls_seen = False

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    yield "data: [DONE]\n\n"
                    break

                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                # Text output
                if event_type == "response.output_text.delta":
                    delta_text = event.get("delta", "")
                    if delta_text:
                        chunk = _make_completion_chunk(chunk_id, model, content=delta_text)
                        yield f"data: {json.dumps(chunk)}\n\n"

                # Tool call completed (use output_item.done for full result)
                elif event_type == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        tool_calls_seen = True
                        tc = {
                            "index": 0,
                            "id": item.get("call_id", f"call_{uuid.uuid4().hex[:8]}"),
                            "type": "function",
                            "function": {
                                "name": item.get("name", ""),
                                "arguments": item.get("arguments", "{}"),
                            },
                        }
                        chunk = _make_completion_chunk(chunk_id, model, tool_calls=[tc])
                        yield f"data: {json.dumps(chunk)}\n\n"

                # Stream completed
                elif event_type == "response.completed":
                    finish = "tool_calls" if tool_calls_seen else "stop"
                    usage_data = None
                    resp_obj = event.get("response", {})
                    if resp_obj.get("usage"):
                        u = resp_obj["usage"]
                        usage_data = {
                            "prompt_tokens": u.get("input_tokens", 0),
                            "completion_tokens": u.get("output_tokens", 0),
                            "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0),
                        }
                    chunk = _make_completion_chunk(
                        chunk_id, model, finish_reason=finish, usage=usage_data
                    )
                    yield f"data: {json.dumps(chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    break


# ─── Public API ──────────────────────────────────────────────────────────────


async def proxy_chat_completion(
    creds: Credentials,
    model: str,
    messages: list[dict],
    stream: bool = True,
    tools: list[dict] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """
    Main entry point: proxy an OpenAI Chat Completions request to Copilot.
    Yields SSE lines in OpenAI Chat Completions format.

    Routing: gpt-5* → /responses, everything else → /chat/completions
    """
    if is_responses_model(model):
        async for chunk in _stream_responses(
            creds, model, messages, stream, tools, temperature, max_tokens
        ):
            yield chunk
    else:
        async for chunk in _stream_completions(
            creds, model, messages, stream, tools, temperature, max_tokens
        ):
            yield chunk
