"""Package init runs before conftest: the app reads its settings at import time."""

import os

os.environ.setdefault("AP_JWT_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("AP_DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")
