"""
Dynamic model listing from the Copilot API.

Fetches models from GET https://api.githubcopilot.com/models
and filters to only chat-capable, enabled models.
"""

from typing import Optional

import httpx

from .auth import Credentials, BASE_URL


async def fetch_models(creds: Credentials) -> list[dict]:
    """
    Fetch available models from the Copilot API.
    Returns filtered list of chat-capable, enabled models.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{BASE_URL}/models",
            headers=creds.headers(),
        )
        resp.raise_for_status()
        data = resp.json()

    # Response can be an array or {"data": [...]}
    if isinstance(data, list):
        raw_models = data
    elif isinstance(data, dict) and "data" in data:
        raw_models = data["data"]
    else:
        return []

    # Filter: model_picker_enabled, chat capability, enabled policy
    models = []
    for m in raw_models:
        if not m.get("model_picker_enabled", False):
            continue
        caps = m.get("capabilities", {})
        if caps.get("type") != "chat":
            continue
        policy = m.get("policy", {})
        if policy.get("state") != "enabled":
            continue
        models.append(m)

    return models


def is_responses_model(model_id: str) -> bool:
    """Check if a model should use the /responses endpoint (GPT-5+)."""
    return model_id.startswith("gpt-5")


def to_openai_model_object(m: dict) -> dict:
    """Convert a Copilot model object to OpenAI /v1/models format."""
    return {
        "id": m.get("id", ""),
        "object": "model",
        "created": 1700000000,
        "owned_by": m.get("vendor", "github-copilot"),
        "permission": [],
        "root": m.get("id", ""),
        "parent": None,
    }
