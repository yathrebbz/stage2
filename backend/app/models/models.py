"""
models.py — Modèles SQLAlchemy (async) + Pydantic Schemas
=========================================================
Entités PostgreSQL du système Smart Room.
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey,
    Integer, String, Text, JSON, Index, func
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship

from app.core.database import Base


# ─── Mixins ──────────────────────────────────────────────────

class TimestampMixin:
    """Mixin pour colonnes created_at / updated_at automatiques."""
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


class UUIDMixin:
    """Mixin pour clé primaire UUID."""
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)


# ─── Enums ────────────────────────────────────────────────────

class UserRole(str, PyEnum):
    ADMIN    = "admin"
    OPERATOR = "operator"
    VIEWER   = "viewer"


class DeviceType(str, PyEnum):
    ESP32        = "esp32"
    STM32        = "stm32"
    RASPBERRY_PI = "raspberry_pi"
    SENSOR_HUB   = "sensor_hub"


class SensorTypeEnum(str, PyEnum):
    TEMPERATURE  = "temperature"
    HUMIDITY     = "humidity"
    LUMINOSITY   = "luminosity"
    PRESENCE     = "presence"
    VOLTAGE      = "voltage"
    CURRENT      = "current"
    POWER        = "power"
    AIR_QUALITY  = "air_quality"
    SURFACE_TEMP = "surface_temp"


class AlertSeverity(str, PyEnum):
    INFO      = "info"
    WARNING   = "warning"
    CRITICAL  = "critical"
    EMERGENCY = "emergency"


class AlertTypeEnum(str, PyEnum):
    THRESHOLD_EXCEEDED = "threshold_exceeded"
    ANOMALY_DETECTED   = "anomaly_detected"
    DEVICE_OFFLINE     = "device_offline"
    BUDGET_EXCEEDED    = "budget_exceeded"
    PREDICTION_ALERT   = "prediction_alert"


class CommandStatus(str, PyEnum):
    PENDING  = "pending"
    SENT     = "sent"
    EXECUTED = "executed"
    FAILED   = "failed"
    TIMEOUT  = "timeout"


class PredictionType(str, PyEnum):
    ENERGY_24H           = "energy_24h"
    ENERGY_7D            = "energy_7d"
    MONTHLY_BILL         = "monthly_bill"
    ANOMALY_SCORE        = "anomaly_score"
    COMFORT_OPTIMIZATION = "comfort_optimization"


# ─── Modèles ──────────────────────────────────────────────────

class User(UUIDMixin, TimestampMixin, Base):
    """Utilisateur du système."""
    __tablename__ = "users"

    email           = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name       = Column(String(255), nullable=False)
    role            = Column(Enum(UserRole), default=UserRole.VIEWER, nullable=False)
    is_active       = Column(Boolean, default=True, nullable=False)
    last_login      = Column(DateTime(timezone=True), nullable=True)

    # Relations
    rooms       = relationship("Room", back_populates="owner", lazy="selectin")
    preferences = relationship("UserPreferences", back_populates="user", uselist=False)
    commands    = relationship("ActuatorCommand", back_populates="issued_by_user")

    def __repr__(self) -> str:
        return f"<User {self.email} [{self.role}]>"


class UserPreferences(UUIDMixin, Base):
    """Préférences utilisateur (tarifs, budgets, alertes)."""
    __tablename__ = "user_preferences"

    user_id            = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                                unique=True, nullable=False)
    electricity_rate   = Column(Float, default=0.20, nullable=False)  # €/kWh
    currency           = Column(String(10), default="EUR", nullable=False)
    budget_monthly_eur = Column(Float, default=50.0, nullable=False)
    alert_thresholds   = Column(JSONB, default=dict, nullable=False)
    timezone           = Column(String(50), default="Africa/Tunis", nullable=False)
    notification_prefs = Column(JSONB, default=dict, nullable=False)

    user = relationship("User", back_populates="preferences")


class Room(UUIDMixin, TimestampMixin, Base):
    """Pièce/salle surveillée."""
    __tablename__ = "rooms"

    name        = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    location    = Column(String(255), nullable=True)
    is_active   = Column(Boolean, default=True, nullable=False)
    owner_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Relations
    owner             = relationship("User", back_populates="rooms")
    devices           = relationship("Device", back_populates="room", lazy="selectin")
    alerts            = relationship("Alert", back_populates="room")
    financial_records = relationship("FinancialRecord", back_populates="room")
    ml_predictions    = relationship("MLPrediction", back_populates="room")
    automation_rules  = relationship("AutomationRule", back_populates="room")

    def __repr__(self) -> str:
        return f"<Room {self.name}>"


class Device(UUIDMixin, TimestampMixin, Base):
    """Dispositif IoT (ESP32, STM32, Raspberry Pi)."""
    __tablename__ = "devices"

    room_id          = Column(UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"),
                              nullable=False)
    device_type      = Column(Enum(DeviceType), nullable=False)
    device_name      = Column(String(255), nullable=False)
    mqtt_topic       = Column(String(500), nullable=True)
    mac_address      = Column(String(17), nullable=True, unique=True)
    firmware_version = Column(String(50), nullable=True)
    last_seen        = Column(DateTime(timezone=True), nullable=True)
    is_online        = Column(Boolean, default=False, nullable=False)
    config           = Column(JSONB, default=dict, nullable=False)
    api_key_hash     = Column(String(255), nullable=True)  # Hash de la device API key

    room     = relationship("Room", back_populates="devices")
    commands = relationship("ActuatorCommand", back_populates="device")

    __table_args__ = (
        Index("idx_device_room_type", "room_id", "device_type"),
    )


class Alert(UUIDMixin, TimestampMixin, Base):
    """Alerte générée par le système."""
    __tablename__ = "alerts"

    room_id      = Column(UUID(as_uuid=True), ForeignKey("rooms.id"), nullable=False)
    alert_type   = Column(Enum(AlertTypeEnum), nullable=False)
    severity     = Column(Enum(AlertSeverity), nullable=False)
    message      = Column(Text, nullable=False)
    sensor_type  = Column(Enum(SensorTypeEnum), nullable=True)
    value        = Column(Float, nullable=True)
    threshold    = Column(Float, nullable=True)
    acknowledged = Column(Boolean, default=False, nullable=False)
    ack_by       = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    ack_at       = Column(DateTime(timezone=True), nullable=True)

    room = relationship("Room", back_populates="alerts")

    __table_args__ = (
        Index("idx_alert_room_ack", "room_id", "acknowledged"),
        Index("idx_alert_created", "created_at"),
    )


class FinancialRecord(UUIDMixin, TimestampMixin, Base):
    """Enregistrement financier mensuel."""
    __tablename__ = "financial_records"

    room_id           = Column(UUID(as_uuid=True), ForeignKey("rooms.id"), nullable=False)
    period_start      = Column(DateTime(timezone=True), nullable=False)
    period_end        = Column(DateTime(timezone=True), nullable=False)
    total_kwh         = Column(Float, default=0.0, nullable=False)
    total_cost        = Column(Float, default=0.0, nullable=False)
    budget            = Column(Float, nullable=True)
    currency          = Column(String(10), default="EUR", nullable=False)
    tariff_rate       = Column(Float, nullable=False)  # €/kWh au moment de l'enregistrement
    anomaly_detected  = Column(Boolean, default=False, nullable=False)
    ai_recommendation = Column(Text, nullable=True)
    metadata_json     = Column(JSONB, default=dict, nullable=False)

    room = relationship("Room", back_populates="financial_records")

    __table_args__ = (
        Index("idx_financial_room_period", "room_id", "period_start"),
    )


class MLPrediction(UUIDMixin, Base):
    """Prédiction ML stockée."""
    __tablename__ = "ml_predictions"

    room_id          = Column(UUID(as_uuid=True), ForeignKey("rooms.id"), nullable=False)
    model_name       = Column(String(100), nullable=False)
    model_version    = Column(String(50), nullable=True)
    prediction_type  = Column(Enum(PredictionType), nullable=False)
    predicted_value  = Column(Float, nullable=True)
    confidence       = Column(Float, nullable=True)  # 0.0 - 1.0
    horizon_hours    = Column(Integer, nullable=True)
    features_used    = Column(JSONB, default=dict, nullable=False)
    result_json      = Column(JSONB, default=dict, nullable=False)  # Données complètes
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    valid_until      = Column(DateTime(timezone=True), nullable=True)

    room = relationship("Room", back_populates="ml_predictions")

    __table_args__ = (
        Index("idx_prediction_room_type", "room_id", "prediction_type"),
        Index("idx_prediction_created", "created_at"),
    )


class ActuatorCommand(UUIDMixin, Base):
    """Commande envoyée à un actuateur."""
    __tablename__ = "actuator_commands"

    device_id    = Column(UUID(as_uuid=True), ForeignKey("devices.id"), nullable=False)
    command_type = Column(String(50), nullable=False)
    payload      = Column(JSONB, default=dict, nullable=False)
    status       = Column(Enum(CommandStatus), default=CommandStatus.PENDING, nullable=False)
    issued_by    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    executed_at  = Column(DateTime(timezone=True), nullable=True)
    error_msg    = Column(Text, nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    device       = relationship("Device", back_populates="commands")
    issued_by_user = relationship("User", back_populates="commands")


class AutomationRule(UUIDMixin, TimestampMixin, Base):
    """Règle d'automatisation (trigger → action)."""
    __tablename__ = "automation_rules"

    room_id     = Column(UUID(as_uuid=True), ForeignKey("rooms.id"), nullable=False)
    name        = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    trigger     = Column(JSONB, nullable=False)   # {metric, operator, value, duration_min}
    action      = Column(JSONB, nullable=False)    # {actuator, command, payload}
    enabled     = Column(Boolean, default=True, nullable=False)
    last_fired  = Column(DateTime(timezone=True), nullable=True)
    fire_count  = Column(Integer, default=0, nullable=False)

    room = relationship("Room", back_populates="automation_rules")
