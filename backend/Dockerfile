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
RUN pip install --upgrade pip \
    && pip install "fastapi>=0.111" "uvicorn[standard]>=0.30" "sqlalchemy[asyncio]>=2.0" \
                   "asyncpg>=0.29" "alembic>=1.13" "redis>=5.0" "httpx>=0.27" \
                   "pydantic>=2.7" "pydantic-settings>=2.3" "structlog>=24.1" \
                   "tenacity>=8.3" "aiocache>=0.12" "feedparser>=6.0.18" \
                   "python-telegram-bot>=21.3" "openai>=1.40" "anthropic>=0.30" \
                   "google-generativeai>=0.7" "numpy>=1.26" \
                   "pytest>=8.2" "pytest-asyncio>=0.23" "pytest-cov>=5.0" \
                   "ruff>=0.5" "respx>=0.21"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]