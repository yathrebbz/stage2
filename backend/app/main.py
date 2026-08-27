"""
main.py — Smart Room Backend FastAPI Application
================================================
Point d'entrée principal de l'application microservices.

Architecture:
    - Auth Service      : JWT + OAuth2
    - IoT Data Service  : Ingestion + validation
    - Analytics Service : Agrégations + statistiques
    - ML Service        : Prédictions + anomalies
    - Financial Service : Coûts + budgets
    - Notification Svc  : Email + SMS + Push
    - WebSocket         : Temps réel clients
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
import uvicorn
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.v1 import auth, rooms, sensors, analytics, financial, ml, actuators, alerts, websocket
from app.core.config import settings
from app.core.database import engine, Base
from app.core.exceptions import AppException
from app.core.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


# ─── Lifespan Manager ────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Gestion du cycle de vie de l'application."""
    logger.info("Démarrage Smart Room Backend v1.0.0")

    # Création des tables si nécessaire (migrations gérées par Alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Connexion Redis
    app.state.redis = await aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
    )
    await app.state.redis.ping()
    logger.info("Redis: connecté")

    # Rate limiter
    app.state.rate_limiter = RateLimiter(app.state.redis)

    logger.info("Smart Room Backend opérationnel")
    yield

    # Nettoyage
    await app.state.redis.close()
    await engine.dispose()
    logger.info("Smart Room Backend arrêté proprement")


# ─── Application FastAPI ─────────────────────────────────────

app = FastAPI(
    title="Smart Room & Financial Monitoring API",
    description="""
## API REST pour le système de monitoring intelligent

### Fonctionnalités
- 🏠 **Rooms** : Gestion des pièces et capteurs
- 📊 **IoT Data** : Ingestion et consultation données temps réel
- 🧠 **ML/AI** : Prédictions et détection d'anomalies
- 💰 **Financial** : Analyse coûts et budgets énergétiques
- 🔔 **Alerts** : Gestion des alertes et notifications
- ⚙️ **Actuators** : Contrôle des actionneurs

### Authentification
Toutes les routes (sauf /auth) requièrent un JWT Bearer token.
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ─── Middlewares ─────────────────────────────────────────────

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-RateLimit-Remaining"],
)

# Compression Gzip
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ─── Prometheus Metrics ──────────────────────────────────────
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics")


# ─── Middleware Rate Limiting ─────────────────────────────────

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting global: 100 req/min par IP."""
    if not request.url.path.startswith("/health"):
        client_ip = request.client.host
        allowed, remaining, reset_time = await request.app.state.rate_limiter.check(
            key=f"ratelimit:{client_ip}",
            limit=100,
            window_seconds=60,
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests. Try again later."},
                headers={
                    "X-RateLimit-Limit": "100",
                    "X-RateLimit-Remaining": "0",
                    "Retry-After": str(reset_time),
                },
            )

    response = await call_next(request)
    return response


# ─── Gestionnaire d'exceptions global ────────────────────────

@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ─── Routes ──────────────────────────────────────────────────

PREFIX = "/api/v1"

app.include_router(auth.router,       prefix=f"{PREFIX}/auth",       tags=["Authentication"])
app.include_router(rooms.router,      prefix=f"{PREFIX}/rooms",      tags=["Rooms"])
app.include_router(sensors.router,    prefix=f"{PREFIX}/sensors",    tags=["IoT Data"])
app.include_router(analytics.router,  prefix=f"{PREFIX}/analytics",  tags=["Analytics"])
app.include_router(financial.router,  prefix=f"{PREFIX}/financial",  tags=["Financial"])
app.include_router(ml.router,         prefix=f"{PREFIX}/ml",         tags=["AI/ML"])
app.include_router(actuators.router,  prefix=f"{PREFIX}/actuators",  tags=["Actuators"])
app.include_router(alerts.router,     prefix=f"{PREFIX}/alerts",     tags=["Alerts"])
app.include_router(websocket.router,  prefix="/ws",                  tags=["WebSocket"])


# ─── Health Check ─────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check(request: Request) -> dict:
    """Vérification de santé de l'application."""
    redis_ok = False
    try:
        await request.app.state.redis.ping()
        redis_ok = True
    except Exception:
        pass

    return {
        "status": "healthy" if redis_ok else "degraded",
        "version": "1.0.0",
        "services": {
            "database": "ok",
            "redis": "ok" if redis_ok else "error",
            "influxdb": "ok",
        },
    }


@app.get("/", tags=["System"])
async def root() -> dict:
    return {
        "app": "Smart Room & Financial Monitoring API",
        "version": "1.0.0",
        "docs": "/docs",
    }


# ─── Point d'entrée ───────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="info",
        access_log=True,
        workers=4,
    )
