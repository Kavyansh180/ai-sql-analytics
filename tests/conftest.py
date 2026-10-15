"""Pytest configuration and fixtures for Phase 1 testing."""
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.core.config import Settings, get_settings
from src.db.session import analytics_engine, app_engine


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Fixture providing loaded environment settings."""
    return get_settings()


@pytest.fixture(scope="session")
def app_db_engine() -> Engine:
    """Fixture providing the SQLAlchemy engine for app_user."""
    return app_engine


@pytest.fixture(scope="session")
def analytics_db_engine() -> Engine:
    """Fixture providing the SQLAlchemy engine for analytics_user."""
    return analytics_engine
