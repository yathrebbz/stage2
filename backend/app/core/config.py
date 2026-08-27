"""
config.py — Configuration centralisée via Pydantic Settings
===========================================================
Toutes les variables d'environnement validées au démarrage.
Pas de secrets hardcodés — tout vient de .env ou variables système.
"""

from functools import lru_cache
from typing import List, Optional

from pydantic import AnyHttpUrl, EmailStr, validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Configuration de l'application.

    Variables d'environnement requises (voir .env.example) :
        DATABASE_URL, SECRET_KEY, INFLUXDB_TOKEN, REDIS_URL
    """

    # ── Application ──────────────────────────────────────
    APP_NAME: str = "Smart Room API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "production"  # development | staging | production

    # ── Sécurité JWT ─────────────────────────────────────
    SECRET_KEY: str  # Obligatoire — générer: openssl rand -hex 32
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Base de données PostgreSQL ────────────────────────
    DATABASE_URL: str  # postgresql+asyncpg://user:pass@host:5432/dbname
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # ── InfluxDB ─────────────────────────────────────────
    INFLUXDB_URL: str = "http://localhost:8086"
    INFLUXDB_TOKEN: str  # Obligatoire
    INFLUXDB_ORG: str = "smart_room"
    INFLUXDB_BUCKET: str = "sensor_data"
    INFLUXDB_BUCKET_AGG: str = "sensor_aggregated"

    # ── Redis ────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CACHE_TTL: int = 300  # 5 minutes

    # ── MQTT (pour publication depuis backend) ────────────
    MQTT_BROKER: str = "localhost"
    MQTT_PORT: int = 8883
    MQTT_USERNAME: str = "backend_service"
    MQTT_PASSWORD: str
    MQTT_CA_CERT: str = "/etc/ssl/smartroom/ca.crt"

    # ── Notifications ────────────────────────────────────
    SENDGRID_API_KEY: Optional[str] = None
    SENDGRID_FROM_EMAIL: EmailStr = "noreply@smartroom.local"
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_FROM_NUMBER: Optional[str] = None

    # ── ML Service ───────────────────────────────────────
    ML_SERVICE_URL: str = "http://ml_service:8001"
    ML_MODEL_PATH: str = "/app/models"
    MLFLOW_TRACKING_URI: str = "http://mlflow:5000"

    # ── CORS ─────────────────────────────────────────────
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "https://smartroom.example.com",
    ]

    # ── Rate Limiting ─────────────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = 100
    RATE_LIMIT_PER_MINUTE_AUTH: int = 10  # Plus strict sur /auth

    # ── Device API Keys ──────────────────────────────────
    DEVICE_API_KEY_HEADER: str = "X-API-Key"

    # ── Monitoring ───────────────────────────────────────
    SENTRY_DSN: Optional[str] = None
    LOG_LEVEL: str = "INFO"

    @validator("ALLOWED_ORIGINS", pre=True)
    @classmethod
    def parse_origins(cls, v):
        """Parse les origines depuis une string CSV ou liste."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    @validator("DATABASE_URL")
    @classmethod
    def validate_db_url(cls, v):
        """S'assure que le driver asyncpg est utilisé."""
        if "postgresql://" in v and "asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://")
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Singleton des settings — chargé une fois au démarrage."""
    return Settings()


settings = get_settings()
