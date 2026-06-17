FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY src/ ./src/

RUN uv sync --no-dev --frozen

ENV HUBSPOT_EMAIL_MCP_TRANSPORT=streamable-http

CMD ["uv", "run", "hubspot-email-mcp"]
