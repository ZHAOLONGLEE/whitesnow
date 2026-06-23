from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # App
    app_env: str = "development"
    app_secret: str = "change-me"

    # NAS Media Library
    media_root: str = "/media"  # Path inside container (mounted from host)
    media_mount: str = "/media"

    # Database
    database_url: str = "sqlite:///./data/mediascan.db"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Admin (future use)
    admin_username: str = "admin"
    admin_password: str = "changeme"

    # Metadata Scraping
    douban_api_url: str = ""
    douban_api_token: str = ""
    tmdb_api_key: str = ""
    
    # Cover storage
    cover_storage: str = "/app/static/covers"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
