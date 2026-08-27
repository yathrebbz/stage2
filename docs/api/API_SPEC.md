# Modèle de Base de Données — ERD & Spécification API

## ERD — PostgreSQL (données relationnelles)

```
┌──────────────────────┐         ┌──────────────────────┐
│        users         │         │       rooms           │
├──────────────────────┤         ├──────────────────────┤
│ id (UUID) PK         │    ┌───►│ id (UUID) PK         │
│ email (VARCHAR) UQ   │    │    │ name (VARCHAR)       │
│ hashed_password      │    │    │ description          │
│ full_name            │    │    │ location             │
│ role (ENUM)          │    │    │ created_at           │
│ is_active (BOOL)     │    │    │ owner_id FK→users    │
│ created_at           │    │    └──────────┬───────────┘
│ last_login           │    │               │
└──────────┬───────────┘    │    ┌──────────▼───────────┐
           │                │    │       devices         │
           │ 1:N            │    ├──────────────────────┤
           ▼                │    │ id (UUID) PK         │
┌──────────────────────┐    │    │ room_id FK→rooms     │
│   user_preferences   │    │    │ device_type (ENUM)   │
├──────────────────────┤    │    │ device_name          │
│ id (UUID) PK         │    │    │ mqtt_topic           │
│ user_id FK→users     │    │    │ mac_address          │
│ electricity_rate     │    │    │ firmware_version     │
│ currency             │    │    │ last_seen            │
│ budget_monthly       │    │    │ is_online (BOOL)     │
│ alert_thresholds     │    │    │ config (JSONB)       │
│ timezone             │    │    └──────────┬───────────┘
│ notification_prefs   │    │               │
└──────────────────────┘    │    ┌──────────▼───────────┐
                             │    │  sensor_readings     │  ← (InfluxDB plutôt)
┌──────────────────────┐    │    ├──────────────────────┤
│   financial_records  │    │    │ id (UUID) PK         │
├──────────────────────┤    │    │ device_id FK→devices │
│ id (UUID) PK         │    │    │ sensor_type (ENUM)   │
│ room_id FK→rooms  ───┘    │    │ value (FLOAT)        │
│ period_start             │    │ unit (VARCHAR)       │
│ period_end               │    │ quality (FLOAT)      │
│ total_kwh (FLOAT)        │    │ timestamp            │
│ total_cost (FLOAT)        │    └──────────────────────┘
│ budget (FLOAT)            │
│ currency                  │    ┌──────────────────────┐
│ tariff_rate               │    │      alerts          │
│ anomaly_detected (BOOL)   │    ├──────────────────────┤
│ ai_recommendation (TEXT)  │    │ id (UUID) PK         │
│ created_at                │    │ room_id FK→rooms     │
└──────────────────────┘    │    │ alert_type (ENUM)    │
                             │    │ severity (ENUM)      │
┌──────────────────────┐    │    │ message (TEXT)       │
│    ml_predictions    │    │    │ value (FLOAT)        │
├──────────────────────┤    │    │ threshold (FLOAT)    │
│ id (UUID) PK         │    │    │ acknowledged (BOOL)  │
│ room_id FK→rooms  ───┘    │    │ created_at           │
│ model_name               │    └──────────────────────┘
│ prediction_type (ENUM)    │
│ predicted_value (FLOAT)   │    ┌──────────────────────┐
│ confidence (FLOAT)        │    │  actuator_commands   │
│ horizon_hours (INT)       │    ├──────────────────────┤
│ features_used (JSONB)     │    │ id (UUID) PK         │
│ created_at                │    │ device_id FK→devices │
└──────────────────────┘    │    │ command_type (ENUM)  │
                             │    │ payload (JSONB)      │
                             │    │ status (ENUM)        │
                             │    │ issued_by FK→users   │
                             │    │ executed_at          │
                             └───►│ created_at           │
                                  └──────────────────────┘
```

## ENUMs PostgreSQL

```sql
CREATE TYPE user_role AS ENUM ('admin', 'operator', 'viewer');
CREATE TYPE device_type AS ENUM ('esp32', 'stm32', 'raspberry_pi', 'sensor_hub');
CREATE TYPE sensor_type_enum AS ENUM (
    'temperature', 'humidity', 'luminosity', 'presence',
    'voltage', 'current', 'power', 'air_quality', 'surface_temp'
);
CREATE TYPE alert_severity AS ENUM ('info', 'warning', 'critical', 'emergency');
CREATE TYPE alert_type_enum AS ENUM (
    'threshold_exceeded', 'anomaly_detected', 'device_offline',
    'budget_exceeded', 'prediction_alert'
);
CREATE TYPE command_status AS ENUM ('pending', 'sent', 'executed', 'failed', 'timeout');
CREATE TYPE prediction_type AS ENUM (
    'energy_24h', 'energy_7d', 'monthly_bill',
    'anomaly_score', 'comfort_optimization'
);
```

## InfluxDB — Schema Séries Temporelles

```
Measurement: sensor_data
Tags (indexés):
  - room_id (string)
  - device_id (string)
  - sensor_type (string)
  - location (string)

Fields (valeurs):
  - value (float)
  - raw_value (float)
  - quality_score (float)    # 0.0 - 1.0 (confiance mesure)
  - anomaly_score (float)    # issu du modèle ML temps réel

Timestamp: nanoseconde précision

Rétention:
  - raw: 7 jours (résolution 100ms)
  - 1min_agg: 90 jours
  - 1hour_agg: 2 ans
  - 1day_agg: indéfini
```

---

# Spécification API REST — FastAPI

## Base URL
- Dev: `http://localhost:8000/api/v1`
- Prod: `https://api.smartroom.example.com/api/v1`

## Authentification

```
POST /auth/register
Body: { email, password, full_name }
Response 201: { user_id, email, full_name }

POST /auth/login
Body: { email, password }
Response 200: { access_token, refresh_token, token_type, expires_in }

POST /auth/refresh
Header: Authorization: Bearer <refresh_token>
Response 200: { access_token, expires_in }

POST /auth/logout
Header: Authorization: Bearer <access_token>
Response 204: (no content)

GET /auth/me
Header: Authorization: Bearer <access_token>
Response 200: { id, email, full_name, role, last_login }
```

## Rooms

```
GET    /rooms                    → Liste des salles de l'utilisateur
POST   /rooms                    → Créer une salle
GET    /rooms/{room_id}          → Détail d'une salle
PUT    /rooms/{room_id}          → Modifier une salle
DELETE /rooms/{room_id}          → Supprimer (soft delete)

GET    /rooms/{room_id}/status   → État temps réel (dernières lectures)
Response: {
  temperature: { value: 22.5, unit: "°C", timestamp: "...", trend: "stable" },
  humidity: { value: 45.2, unit: "%", timestamp: "..." },
  luminosity: { value: 350, unit: "lux", timestamp: "..." },
  presence: { detected: true, timestamp: "..." },
  power: { value: 1250.5, unit: "W", timestamp: "..." },
  air_quality: { aqi: 42, co2_ppm: 650, timestamp: "..." }
}
```

## Données capteurs (IoT Data Service)

```
GET /rooms/{room_id}/sensors/history
Query params:
  - sensor_type: temperature|humidity|luminosity|presence|power|air_quality
  - start: ISO8601 datetime
  - end: ISO8601 datetime
  - resolution: raw|1m|5m|1h|1d
  - aggregation: mean|min|max|sum
Response 200: {
  data: [{ timestamp, value, unit, quality_score }],
  stats: { min, max, mean, std_dev, count }
}

POST /devices/{device_id}/data   ← Ingestion depuis Raspberry Pi
Header: X-API-Key: <device_key>
Body: {
  readings: [
    { sensor_type, value, unit, quality, timestamp }
  ]
}
Response 202: { accepted: 12, rejected: 0, batch_id: "..." }

GET /devices
GET /devices/{device_id}
PUT /devices/{device_id}/config
POST /devices/{device_id}/reboot   ← commande OTA/reboot
```

## Analytics

```
GET /analytics/rooms/{room_id}/summary
Query: period=today|week|month|year
Response: {
  energy: { total_kwh, cost_eur, vs_previous_period_pct },
  comfort: { avg_temp, avg_humidity, comfort_score_0_100 },
  presence: { total_hours, peak_hours },
  anomalies: { count, severity_breakdown }
}

GET /analytics/rooms/{room_id}/heatmap
Query: metric=temperature|power, date=YYYY-MM-DD
Response: { matrix: [[...]], hours: [...], days: [...] }

GET /analytics/rooms/{room_id}/correlations
Response: { correlations: [{ x_metric, y_metric, pearson_r, p_value }] }
```

## Financial Service

```
GET /financial/rooms/{room_id}/current-month
Response: {
  consumed_kwh: 145.3,
  cost_eur: 29.06,
  budget_eur: 50.0,
  budget_remaining_eur: 20.94,
  days_remaining: 8,
  projected_total_eur: 34.5,
  ai_prediction_eur: 36.2,
  savings_vs_last_month_pct: -12.3
}

GET /financial/rooms/{room_id}/history
Query: months=12
Response: { monthly_records: [{ year, month, kwh, cost, budget }] }

POST /financial/rooms/{room_id}/budget
Body: { monthly_budget_eur, alert_at_pct: 80 }

GET /financial/rooms/{room_id}/recommendations
Response: {
  recommendations: [
    {
      category: "scheduling",
      description: "Éteindre la climatisation entre 23h et 6h",
      estimated_saving_eur_month: 8.50,
      confidence: 0.87,
      priority: "high"
    }
  ]
}
```

## AI/ML Service

```
GET /ml/rooms/{room_id}/anomalies
Query: start, end, min_severity=0.7
Response: {
  anomalies: [
    {
      timestamp, sensor_type, value, expected_value,
      anomaly_score, model_used, severity
    }
  ]
}

GET /ml/rooms/{room_id}/predictions/energy
Query: horizon_hours=24
Response: {
  predictions: [{ timestamp, predicted_kwh, lower_bound, upper_bound }],
  model: "LSTM_v2.3",
  confidence: 0.89,
  generated_at: "..."
}

POST /ml/rooms/{room_id}/retrain
Body: { model_type: "anomaly|prediction", force: false }
Response 202: { job_id, estimated_duration_minutes: 15 }

GET /ml/jobs/{job_id}
Response: { status: "running|completed|failed", progress_pct, metrics }
```

## Actuateurs & Contrôle

```
GET  /rooms/{room_id}/actuators          → État tous actuateurs
POST /rooms/{room_id}/actuators/{id}/command
Body: {
  command: "set_relay"|"set_servo"|"set_display",
  payload: { channel: 1, state: true }  // ou angle: 90
}
Response 200: { command_id, status: "sent", estimated_execution_ms: 200 }

GET /rooms/{room_id}/automation-rules
POST /rooms/{room_id}/automation-rules
Body: {
  name: "Éteindre lumière si absent > 10min",
  trigger: { metric: "presence", operator: "eq", value: false, duration_min: 10 },
  action: { actuator: "relay_1", command: "off" },
  enabled: true
}
DELETE /rooms/{room_id}/automation-rules/{rule_id}
```

## Notifications & Alertes

```
GET /alerts?room_id=...&acknowledged=false&severity=critical
POST /alerts/{alert_id}/acknowledge
GET /notifications/preferences
PUT /notifications/preferences
Body: {
  email_enabled: true,
  sms_enabled: false,
  push_enabled: true,
  alert_thresholds: {
    temperature_max: 30,
    humidity_max: 70,
    budget_alert_pct: 80
  }
}
```

## WebSocket Events

```
WS /ws/rooms/{room_id}
Auth: ?token=<access_token>

Server → Client events:
  "sensor_update"    : { type, value, unit, timestamp }
  "alert_triggered"  : { alert_id, type, severity, message }
  "prediction_ready" : { model, horizon, predictions }
  "device_status"    : { device_id, online, firmware_version }
  "command_executed" : { command_id, status, result }

Client → Server events:
  "subscribe_metrics" : { metrics: ["temperature", "power"] }
  "send_command"      : { actuator_id, command, payload }
```
