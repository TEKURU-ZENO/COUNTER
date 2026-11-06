"""
Global pytest configuration for COUNTER test suite.
Ensures tests always run against an isolated local SQLite database
regardless of host or .env PostgreSQL settings.
"""

import os
import pytest

# Force test database URL before any application module imports session
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_voice_agent.db"
