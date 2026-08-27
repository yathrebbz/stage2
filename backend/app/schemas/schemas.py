"""
Pydantic v2 schemas — Smart Room System
Request/Response models for all API endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


# ═══════════════════════════════════════
# BASE / SHARED
# ═══════════════════════════════════════

class TimestampMixin(BaseModel):
    created_at: datetime
    updated_at: datetime


# ═══════════════════════════════════════
# AUTH
# ═══════════════════════════════════════

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=100)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(TimestampMixin):
    id: UUID
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    last_login: datetime | None = None

    model_config = {"from_attributes": True}


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(BaseModel):
    refresh_token: str


# ═══════════════════════════════════════
# ROOMS
# ═══════════════════════════════════════

class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class RoomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None


class RoomOut(TimestampMixin):
    id: UUID
    name: str
    description: str | None
    owner_id: UUID
    device_count: int = 0

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════
# DEVICES
# ═══════════════════════════════════════

class DeviceCreate(BaseModel):
    room_id: UUID
    device_type: str
    name: str = Field(min_length=1, max_length=100)
    mqtt_topic: str = Field(min_length=1, max_length=200)
    mac_address: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class DeviceOut(TimestampMixin):
    id: UUID
    room_id: UUID
    device_type: str
    name: str
    mqtt_topic: str
    mac_address: str | None
    firmware_version: str | None
    is_online: bool
    last_seen: datetime | None
    config: dict[str, Any]

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════
# SENSOR DATA
# ═══════════════════════════════════════

class SensorReading(BaseModel):
    """Inbound sensor payload from device."""
    device_id: UUID
    room_id: UUID
    timestamp: datetime
    temperature: float | None = Field(default=None, ge=-40, le=85)
    humidity: float | None = Field(default=None, ge=0, le=100)
    luminosity_lux: float | None = Field(default=None, ge=0)
    presence: bool | None = None
    power_watts: float | None = Field(default=None, ge=0)
    voltage_v: float | None = Field(default=None, ge=0)
    current_a: float | None = Field(default=None, ge=0)
    co2_ppm: float | None = Field(default=None, ge=0)
    surface_temp: float | None = Field(default=None, ge=-40, le=150)
    comfort_index: float | None = Field(default=None, ge=0, le=100)
    anomaly_flags: int = 0


class SensorReadingOut(BaseModel):
    timestamp: datetime
    sensor_type: str
    value: float
    unit: str
    quality_score: float
    anomaly_score: float | None = None


class SensorQueryParams(BaseModel):
    room_id: UUID
    sensor_type: str
    start: datetime
    end: datetime
    aggregation: str = "1m"  # e.g. "1m", "5m", "1h"


# ═══════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════

class RoomStats(BaseModel):
    room_id: UUID
    period_start: datetime
    period_end: datetime
    temperature_avg: float
    temperature_min: float
    temperature_max: float
    humidity_avg: float
    total_kwh: float
    total_cost_eur: float
    occupancy_hours: float
    comfort_score: float
    anomaly_count: int


# ═══════════════════════════════════════
# FINANCIAL
# ═══════════════════════════════════════

class FinancialSummary(BaseModel):
    period: str
    total_kwh: float
    total_cost_eur: float
    budget_eur: float
    budget_remaining_eur: float
    budget_pct_used: float
    projected_month_cost: float
    vs_previous_period_pct: float
    tariff_rate_eur_kwh: float


class FinancialRecommendation(BaseModel):
    category: str
    description: str
    potential_savings_eur_month: float
    confidence: float
    priority: int


class FinancialHistory(BaseModel):
    records: list[dict[str, Any]]
    total_kwh_ytd: float
    total_cost_ytd: float


# ═══════════════════════════════════════
# ML / AI
# ═══════════════════════════════════════

class AnomalyResult(BaseModel):
    timestamp: datetime
    is_anomaly: bool
    anomaly_score: float
    severity: str  # normal | suspicious | anomaly
    affected_sensors: list[str]
    isolation_forest_score: float
    svm_score: float


class EnergyPrediction(BaseModel):
    room_id: UUID
    model_version: str
    predictions: list[dict[str, Any]]  # {timestamp, predicted_kwh, lower_bound, upper_bound}
    confidence: float
    horizon_hours: int
    generated_at: datetime


class MLModelStatus(BaseModel):
    room_id: UUID
    anomaly_model_trained: bool
    energy_model_trained: bool
    last_training: datetime | None
    anomaly_model_metrics: dict[str, float]
    energy_model_metrics: dict[str, float]


# ═══════════════════════════════════════
# ACTUATORS
# ═══════════════════════════════════════

class ActuatorCommand(BaseModel):
    device_id: UUID
    command_type: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("command_type")
    @classmethod
    def valid_command(cls, v: str) -> str:
        allowed = {"relay_on", "relay_off", "servo_set", "buzzer", "dim_light"}
        if v not in allowed:
            raise ValueError(f"command_type must be one of {allowed}")
        return v


class ActuatorCommandOut(TimestampMixin):
    id: UUID
    device_id: UUID
    command_type: str
    payload: dict[str, Any]
    status: str
    issued_by: UUID
    executed_at: datetime | None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════

class AlertOut(TimestampMixin):
    id: UUID
    room_id: UUID
    alert_type: str
    severity: str
    sensor_type: str | None
    value: float | None
    threshold: float | None
    message: str
    acknowledged: bool
    ack_at: datetime | None

    model_config = {"from_attributes": True}


class AlertAcknowledge(BaseModel):
    alert_id: UUID
    notes: str | None = None


class AlertThresholds(BaseModel):
    temperature_max: float = 30.0
    temperature_min: float = 16.0
    humidity_max: float = 80.0
    humidity_min: float = 30.0
    co2_max: float = 1500.0
    power_max_watts: float = 3000.0
    budget_alert_pct: float = 80.0


# ═══════════════════════════════════════
# AUTOMATION RULES
# ═══════════════════════════════════════

class AutomationRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    trigger: dict[str, Any]  # {sensor_type, operator, value}
    action: dict[str, Any]   # {device_id, command_type, payload}
    enabled: bool = True


class AutomationRuleOut(TimestampMixin):
    id: UUID
    name: str
    description: str | None
    trigger: dict[str, Any]
    action: dict[str, Any]
    enabled: bool
    last_fired: datetime | None
    fire_count: int

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════
# PAGINATION
# ═══════════════════════════════════════

class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int
    pages: int


# ═══════════════════════════════════════
# WEBSOCKET
# ═══════════════════════════════════════

class WSMessage(BaseModel):
    event: str
    room_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class WSSubscribePayload(BaseModel):
    metrics: list[str] = Field(
        default=["temperature", "humidity", "power_watts", "luminosity_lux"]
    )
