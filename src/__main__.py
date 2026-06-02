"""Entry point: python -m src"""

import argparse
import asyncio
import uvicorn

from .auth import load_credentials, login


def main():
    parser = argparse.ArgumentParser(description="GitHub Copilot Proxy Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8484, help="Port to bind (default: 8484)")
    parser.add_argument("--login", action="store_true", help="Force re-login before starting")
    args = parser.parse_args()

    if args.login:
        asyncio.run(login())

    print(f"[copilot-proxy] Starting server on {args.host}:{args.port}")
    print(f"[copilot-proxy] OpenAI base URL: http://{args.host}:{args.port}/v1")
    print()

    uvicorn.run(
        "src.server:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
