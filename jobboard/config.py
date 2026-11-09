"""Central environment configuration."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./hireloop.db")
    site_url: str = os.getenv("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
    contact_email: str = os.getenv("CONTACT_EMAIL", "hello@example.com")
    secret_key: str = os.getenv("SECRET_KEY", "development-only-change-this-secret")
    featured_price_pkr: int = int(os.getenv("FEATURED_PRICE_PKR", "5000"))
    payment_instructions: str = os.getenv(
        "PAYMENT_INSTRUCTIONS",
        f"Contact {os.getenv('CONTACT_EMAIL', 'hello@example.com')} for payment instructions.",
    )
    ads_enabled: bool = os.getenv("ADS_ENABLED", "0") == "1"

    def __post_init__(self):
        if self.site_url.startswith("https://") and (
            len(self.secret_key) < 32
            or self.secret_key == "development-only-change-this-secret"
        ):
            raise RuntimeError("Set a long random SECRET_KEY for production sessions")

    @property
    def admin_emails(self) -> set[str]:
        return {
            email.strip().casefold()
            for email in os.getenv("ADMIN_EMAILS", "").split(",")
            if email.strip()
        }


settings = Settings()
