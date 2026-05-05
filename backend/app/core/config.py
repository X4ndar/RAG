"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["ollama", "claude"]
AppEnv = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """Runtime configuration.

    All values are read from environment variables (or a `.env` file in
    development). No secrets in code.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: AppEnv = "development"
    log_level: str = "INFO"
    secret_key: str = Field(default="change-me", min_length=8)

    database_url: str = "postgresql+asyncpg://rag:rag@postgres:5432/rag"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str | None = None
    redis_url: str = "redis://redis:6379/0"

    backend_cors_origins: str = "http://localhost:3000"

    llm_provider: LLMProvider = "ollama"
    anthropic_api_key: str | None = None
    ollama_host: str = "http://host.docker.internal:11434"

    # 32-byte URL-safe base64 Fernet key. Loaded by app.llm.encryption at
    # call time, NOT cached in the Settings instance. None or malformed
    # surfaces as MasterKeyMissing → 503 from every LLM-touching endpoint.
    llm_config_master_key: str | None = None

    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    # Shared embedding-model weights cache. In-container this points at the
    # `embedding_cache` named volume (/app/.cache/embeddings). On the host
    # it resolves to ~/.cache/embeddings so `uv run pytest` outside Docker
    # still caches the BGE-M3 weights (~2 GB) between runs.
    embedding_cache_dir: str = Field(
        default_factory=lambda: str(Path.home() / ".cache" / "embeddings"),
    )
    # Uploaded-document storage. In-container: /app/storage (named volume
    # `document_storage`, mounted on backend and worker). On host: falls
    # back to ~/.cache/rag-storage so direct-host runs don't write to
    # arbitrary paths.
    storage_dir: str = Field(
        default_factory=lambda: str(Path.home() / ".cache" / "rag-storage"),
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Split the comma-separated CORS origins string into a list."""
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
