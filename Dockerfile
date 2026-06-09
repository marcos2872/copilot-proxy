FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY src/ src/

RUN uv sync --frozen --no-dev

EXPOSE 7677

CMD ["uv", "run", "copilot-proxy", "--host", "0.0.0.0", "--port", "7677"]
