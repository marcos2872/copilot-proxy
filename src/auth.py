"""
GitHub Copilot OAuth Device Flow authentication.

Approach: The oauth_token (ghu_...) is used DIRECTLY as Bearer in all API calls.
No intermediate token exchange needed.
"""

import json
import asyncio
import time
from pathlib import Path
from typing import Optional

import httpx

CLIENT_ID = "Ov23li8tweQw6odWQebz"
BASE_URL = "https://api.githubcopilot.com"

HEADERS_BASE = {
    "User-Agent": "copilot-proxy/1.0",
    "Openai-Intent": "conversation-edits",
    "x-initiator": "user",
    "Content-Type": "application/json",
}

TOKEN_FILE = Path.home() / ".config" / "copilot-proxy" / "token.json"


class Credentials:
    """Holds the OAuth token. Used directly as Bearer — no swap needed."""

    def __init__(self, oauth_token: str):
        self.oauth_token = oauth_token

    def headers(self) -> dict[str, str]:
        """Return headers for all Copilot API calls."""
        return {
            **HEADERS_BASE,
            "Authorization": f"Bearer {self.oauth_token}",
        }

    def to_dict(self) -> dict:
        return {"oauth_token": self.oauth_token}


async def start_device_flow() -> dict:
    """Start the OAuth Device Flow and return device/user codes."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/device/code",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "copilot-proxy/1.0",
            },
            json={"client_id": CLIENT_ID, "scope": "read:user"},
        )
        resp.raise_for_status()
        return resp.json()


async def poll_for_token(device_code: str, interval: int, expires_in: int) -> str:
    """Poll GitHub until the user authorizes, returning the access token."""
    deadline = time.time() + min(expires_in, 600)
    poll_interval = max(interval, 5)

    async with httpx.AsyncClient() as client:
        while time.time() < deadline:
            await asyncio.sleep(poll_interval + 3)
            try:
                resp = await client.post(
                    "https://github.com/login/oauth/access_token",
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "copilot-proxy/1.0",
                    },
                    json={
                        "client_id": CLIENT_ID,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError:
                continue

            if "access_token" in data:
                return data["access_token"]
            elif data.get("error") == "slow_down":
                poll_interval += 5
            elif data.get("error") == "authorization_pending":
                continue
            elif "error" in data:
                raise RuntimeError(
                    f"OAuth error: {data['error']}: {data.get('error_description', '')}"
                )

    raise TimeoutError("Device flow timed out (10 min)")


async def login() -> Credentials:
    """Full Device Flow login. Prints instructions for the user."""
    device = await start_device_flow()
    print()
    print("=" * 50)
    print("  GitHub Copilot Proxy — Authentication")
    print("=" * 50)
    print(f"  1. Open:  {device['verification_uri']}")
    print(f"  2. Enter: {device['user_code']}")
    print("=" * 50)
    print()
    print("Waiting for authorization...")

    oauth_token = await poll_for_token(
        device["device_code"], device["interval"], device["expires_in"]
    )
    print("Authenticated!")

    creds = Credentials(oauth_token=oauth_token)
    _save_credentials(creds)
    return creds


def _save_credentials(creds: Credentials) -> None:
    """Persist credentials to disk."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(creds.to_dict()))
    TOKEN_FILE.chmod(0o600)


def load_credentials() -> Optional[Credentials]:
    """Load saved credentials from disk."""
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text())
        return Credentials(oauth_token=data["oauth_token"])
    except (json.JSONDecodeError, KeyError):
        return None
