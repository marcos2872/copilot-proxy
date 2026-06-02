# AGENTS.md

## Project

OpenAI-compatible proxy that forwards requests to GitHub Copilot API. Allows OpenWebUI and other OpenAI clients to use a Copilot subscription.

## Commands

```bash
uv sync                        # install deps
uv run copilot-proxy           # start server (port 8484)
uv run copilot-proxy --login   # force re-auth before starting
docker compose up -d           # proxy + OpenWebUI together
```

No test suite exists yet.

## Architecture

```
src/
├── __main__.py   # CLI entrypoint (argparse + uvicorn)
├── auth.py       # OAuth Device Flow, token persistence
├── models.py     # Dynamic model listing from Copilot API
├── proxy.py      # Request translation + SSE streaming
└── server.py     # FastAPI app (endpoints)
```

- Package is named `src` in the wheel (`[tool.hatch.build.targets.wheel] packages = ["src"]`).
- uvicorn import string is `"src.server:app"`.
- Internal imports use relative form (`from .auth import ...`).

## Key design decisions

- **No token swap.** The `ghu_...` OAuth token goes directly as Bearer. No intermediate Copilot session token.
- **Dynamic models.** `GET https://api.githubcopilot.com/models` — no hardcoded list.
- **Routing rule:** model ID starts with `gpt-5` → `/responses` endpoint; everything else → `/chat/completions`.
- **Responses API quirk:** `function_call` and `function_call_output` are **root-level items** in the `input` array, not nested inside `content[]`. Placing them inside content causes HTTP 400.

## Conventions

- Python 3.11+, type hints, async/await throughout.
- No linter/formatter configured yet — follow existing style (no trailing commas in function args, double quotes).
- Credentials stored at `~/.config/copilot-proxy/token.json` (chmod 600).
- Client ID: `Ov23li8tweQw6odWQebz`.
- Base URL: `https://api.githubcopilot.com` (single endpoint, no per-plan routing).
