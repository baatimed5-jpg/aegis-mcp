# AegisMCP Python services (gateway, wealth server, malicious test server, migrations).
# Runtime dependencies only: no dev, ML or agent groups (the default demo uses the rule detector and
# rule optimizer; the DeBERTa / learned variants need the "ml" group, see README).
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY gateway ./gateway
COPY servers ./servers
COPY config ./config
ENV PATH="/opt/venv/bin:$PATH"
EXPOSE 8000 8001 8002
CMD ["python", "-m", "gateway", "--config", "config/docker/gateway.yaml", "--host", "0.0.0.0"]
