# Multi-stage build: Vite frontend → Python backend → nginx web tier.
# Build everything with:  docker compose up --build
# (see docker-compose.yml — `web` serves the UI on :8080 and proxies to `backend`)

# ---------- stage 1: frontend build ----------
FROM node:20-alpine AS frontend-build
WORKDIR /fe
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ---------- stage 2: backend ----------
FROM python:3.12-slim AS backend
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Locked install into a project-local venv. The 'data' extra (pvlib + pandas)
# is included so real Open-Meteo PV/wind physics work in the container; the
# 'ai' extra (Ray/torch) is deliberately skipped — the PPO agent only joins
# when EFLUX_PPO_CHECKPOINT is set, which a demo container doesn't do.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra data

COPY alembic.ini ./
COPY alembic ./alembic
COPY scenarios ./scenarios

# SQLite + in-memory market by design: the DB file lives in the container and
# resets with it. Point EFLUX_DB_URL at Postgres for anything longer-lived.
EXPOSE 8000
CMD ["uvicorn", "eflux.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------- stage 3: web (static frontend + reverse proxy) ----------
FROM nginx:1.27-alpine AS web
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /fe/dist /usr/share/nginx/html
