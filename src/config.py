from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    debug_errors: bool = Field(default=False, validation_alias="DEBUG_ERRORS")
    tor_enabled: bool = Field(default=False, validation_alias="TOR_ENABLED")
    tor_socks_proxy: Optional[str] = Field(
        default=None, validation_alias="TOR_SOCKS_PROXY"
    )
    work_dir: Path = Field(default=Path("works"), validation_alias="WORK_DIR")
    headless: bool = Field(default=True, validation_alias="HEADLESS")
    natural_delay_min_s: float = Field(
        default=1.0, validation_alias="NATURAL_DELAY_MIN_S"
    )
    natural_delay_max_s: float = Field(
        default=3.0, validation_alias="NATURAL_DELAY_MAX_S"
    )
    api_key: Optional[str] = Field(default=None, validation_alias="API_KEY")
    browser_pool_size: int = Field(default=2, validation_alias="BROWSER_POOL_SIZE")


settings = Settings()
