import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    tavily_api_key: str
    model_extract: str
    model_write: str
    db_path: Path
    max_suppliers: int
    live_daily_limit: int
    page_ttl_days: int

    @property
    def live_available(self) -> bool:
        return bool(self.anthropic_api_key and self.tavily_api_key)


def get_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        model_extract=os.getenv("MODEL_EXTRACT", "claude-haiku-5-5"),
        model_write=os.getenv("MODEL_WRITE", "claude-sonnet-5-5"),
        db_path=Path(os.getenv("DB_PATH", str(ROOT / "data" / "app.db"))),
        max_suppliers=int(os.getenv("MAX_SUPPLIERS", "12")),
        live_daily_limit=int(os.getenv("LIVE_DAILY_LIMIT", "20")),
        page_ttl_days=int(os.getenv("PAGE_TTL_DAYS", "7")),
    )
