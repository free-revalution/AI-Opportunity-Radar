"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    """A FastAPI TestClient backed by a freshly constructed app instance."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def anyio_backend() -> str:
    """Force asyncio for anyio-using tests."""
    return "asyncio"