import os
from pydantic_settings import BaseSettings, SettingsConfigDict

from typing import List
import logging

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_NAME: str = "Enterprise-Prod-RAG"
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "super-secret-key-change-in-production-32-bytes-minimum"

    # CORS & Limits
    # In production, set CORS_ORIGINS to your specific frontend domains, e.g.:
    # CORS_ORIGINS=["https://app.yourdomain.com"]
    # Never use ["*"] with allow_credentials=True in production.
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080"]
    MAX_UPLOAD_SIZE_BYTES: int = 52428800  # 50MB max upload size limit
    TASK_TIMEOUT_SECONDS: int = 300       # 5 minutes queue timeout

    # PostgreSQL
    POSTGRES_USER: str = "rag_user"
    POSTGRES_PASSWORD: str = "rag_password"
    POSTGRES_DB: str = "rag_db"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str = ""

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_URL: str = "redis://localhost:6379/0"

    # Vector DB
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "documents_collection"

    # MinIO / S3
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET_NAME: str = "rag-documents"
    S3_REGION: str = "us-east-1"

    # Celery & RabbitMQ
    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    CELERY_BROKER_URL: str = "amqp://guest:guest@localhost:5672//"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # Security
    ENABLE_PII_REDACTION: bool = True
    PII_REDACT_PHONE: bool = False
    PII_REDACT_EMAIL: bool = False
    PII_REDACT_PERSON: bool = False
    ENABLE_RATE_LIMITING: bool = True
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 60

    # Observability
    OTEL_COLLECTOR_URL: str = "http://localhost:4317"
    ENABLE_TELEMETRY: bool = True

    # LLM & Embedding Keys
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    COHERE_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    HF_TOKEN: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-20b"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-2"



    def validate_secrets(self):
        if self.APP_ENV != "development":
            if "super-secret-key" in self.SECRET_KEY or len(self.SECRET_KEY) < 32:
                raise ValueError("CRITICAL: Insecure default SECRET_KEY in non-development environment.")
            if self.S3_ACCESS_KEY == "minioadmin" or self.S3_SECRET_KEY == "minioadmin":
                raise ValueError("CRITICAL: Default MinIO credentials (minioadmin) must not be used in non-development environments.")
            if "*" in self.CORS_ORIGINS:
                raise ValueError("CRITICAL: Wildcard CORS_ORIGINS='[*]' is not permitted in non-development environments.")

    def get_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    def get_redis_url(self) -> str:
        if self.REDIS_URL and "localhost" not in self.REDIS_URL and self.REDIS_HOST == "localhost":
            return self.REDIS_URL
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

settings = Settings()
settings.validate_secrets()

