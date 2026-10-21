import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
