"""Central environment configuration."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./hireloop.db")
    site_url: str = os.getenv("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    contact_email: str = os.getenv("CONTACT_EMAIL", "hello@example.com")
    ads_enabled: bool = os.getenv("ADS_ENABLED", "0") == "1"


settings = Settings()
