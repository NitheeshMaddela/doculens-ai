from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DIMENSIONS = 1536
LOCAL_PROFILE = "hash-blake2b-v1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://doculens:doculens-local@127.0.0.1:5434/doculens"
    storage_path: Path = Path(".data")
    demo_access_key: SecretStr = SecretStr("doculens-local")
    provider: Literal["local", "openai"] = "local"
    openai_api_key: SecretStr | None = None
    embedding_model: str = ""
    answer_model: str = ""
    retrieval_k: int = Field(5, ge=1, le=12)
    max_upload_bytes: int = Field(10 * 1024**2, ge=1024, le=50 * 1024**2)
    max_pdf_pages: int = Field(80, ge=1, le=250)
    max_context_tokens: int = Field(3000, ge=500, le=8000)
    max_output_tokens: int = Field(512, ge=128, le=2048)
    query_per_minute: int = Field(12, ge=1)
    query_per_day: int = Field(100, ge=1)
    upload_per_minute: int = Field(6, ge=1)
    upload_per_day: int = Field(30, ge=1)
    document_limit: int = Field(30, ge=1, le=300)
    lease_seconds: int = Field(180, ge=60)
    cors_origins: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_provider(self) -> "Settings":
        if self.provider == "openai" and not (
            self.openai_api_key
            and self.openai_api_key.get_secret_value()
            and self.embedding_model
            and self.answer_model
        ):
            raise ValueError("OpenAI mode requires API key, embedding model and answer model")
        if not self.demo_access_key.get_secret_value():
            raise ValueError("DEMO_ACCESS_KEY must be set")
        return self

    @property
    def profile(self) -> str:
        return LOCAL_PROFILE if self.provider == "local" else f"openai:{self.embedding_model}"


@lru_cache
def settings() -> Settings:
    return Settings()
