import pytest

from app import sysinfo


@pytest.fixture(autouse=True)
def plenty_of_ram(monkeypatch):
    """Tests must not depend on how much RAM the developer machine has free right now."""
    monkeypatch.setattr(sysinfo, "memory", lambda: {"total_mb": 16000, "available_mb": 8000})
