# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS locked-requirements
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /bin/uv
WORKDIR /export
COPY pyproject.toml uv.lock README.md ./
RUN uv --quiet export --frozen --no-dev --no-editable --no-emit-project \
    --format requirements-txt --output-file /requirements.txt

FROM python:3.11-slim-bookworm AS application
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN groupadd --gid 10001 scione \
    && useradd --uid 10001 --gid scione --no-create-home --shell /usr/sbin/nologin scione
COPY pyproject.toml uv.lock README.md ./
# Keep uv and its cache in the exporter stage. Only its hash-locked requirements
# enter the runtime image; this also works on Railway's Docker builder.
COPY --from=locked-requirements /requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .
COPY --from=frontend /frontend/dist/ ./frontend/dist/
ENV SCIONE_ENVIRONMENT=production \
    SCIONE_WORKSPACE_DIR=/data \
    SCIONE_FRONTEND_DIR=/app/frontend/dist
EXPOSE 8080
# Startup prepares the mounted volume, drops root privileges, applies additive
# migrations and then serves the API/frontend on Railway's assigned PORT.
CMD ["python", "-m", "scione.deployment"]
