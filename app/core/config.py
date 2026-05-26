import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    # Database
    database_mode: str = "sqlite"  # "sqlite" or "supabase"
    sqlite_path: str = str(_PROJECT_ROOT / "data" / "persona_tracking.db")
    database_url: Optional[str] = None  # PostgreSQL connection string for Supabase

    # Supabase (optional, for direct Supabase client usage)
    supabase_url: Optional[str] = None
    supabase_service_key: Optional[str] = None
    db_schema: str = "public"  # Postgres search_path; use "staging" or "dev" for non-prod
    db_ssl: bool = True  # Set to false for Railway-internal PostgreSQL (no TLS needed)

    # Auth
    api_key: str = "change-me-in-production"
    # Browser-facing dashboard/docs access code. Set to empty to disable this UI gate.
    tpt_access_code: Optional[str] = "teneraincadmin"
    # Write-only key — safe to embed in browser JS (only grants access to /track)
    write_key: Optional[str] = None

    # LLM for cluster NER + summarization (via litellm)
    anthropic_api_key: Optional[str] = None
    azure_api_key: Optional[str] = None
    azure_api_base: Optional[str] = None
    azure_api_version: Optional[str] = None
    llm_model: str = "azure/gpt-5.4-mini"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": str(_PROJECT_ROOT / ".env"), "extra": "ignore"}

    @property
    def effective_database_url(self) -> str:
        if self.database_mode == "supabase" and self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.sqlite_path}"


settings = Settings()
