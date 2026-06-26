FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
# Slot manifests for the clone-and-fill tools (Phase 2). Resolved at runtime from
# /app/templates (the container CWD). Must ship in the image so fill_email_draft can
# load templates/<name>.json.
COPY templates/ ./templates/

RUN uv sync --no-dev --frozen

ENV HUBSPOT_EMAIL_MCP_TRANSPORT=streamable-http

CMD ["uv", "run", "hubspot-email-mcp"]
