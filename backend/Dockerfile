FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps for asyncpg / cryptography edge cases.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Install runtime deps from pyproject.toml (single source of truth — keeps the
# Dockerfile in sync with `pip install -e .` on host). Dev deps (pytest, ruff,
# respx) are installed separately so production images don't carry them.
RUN pip install --upgrade pip \
    && pip install \
        "fastapi>=0.111" "uvicorn[standard]>=0.30" "sqlalchemy[asyncio]>=2.0" \
        "asyncpg>=0.29" "alembic>=1.13" "psycopg2-binary>=2.9" "redis>=5.0" "httpx>=0.27" \
        "pydantic>=2.7" "pydantic-settings>=2.3" "structlog>=24.1" \
        "tenacity>=8.3" "aiocache>=0.12" "feedparser>=6.0.14" \
        "python-telegram-bot>=21.3" "openai>=1.40" "anthropic>=0.30" "numpy>=1.26" \
        "prometheus-client>=0.20"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]