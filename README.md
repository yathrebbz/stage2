# 🏠 Smart Room & Financial Monitoring System

> **Embedded IoT · AI Analytics · DevOps Deployment**  
> Projet académique Master — Génie Électrique & Systèmes Embarqués

[![CI/CD](https://github.com/your-org/smart-room-system/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/your-org/smart-room-system/actions)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61dafb.svg)](https://reactjs.org)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](https://docker.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table des matières

1. [Vue d'ensemble](#vue-densemble)
2. [Architecture](#architecture)
3. [Prérequis](#prérequis)
4. [Démarrage rapide](#démarrage-rapide)
5. [Hardware & Firmware](#hardware--firmware)
6. [Backend](#backend)
7. [ML Service](#ml-service)
8. [Frontend](#frontend)
9. [DevOps](#devops)
10. [Tests](#tests)
11. [Monitoring](#monitoring)

---

## Vue d'ensemble

Système complet de surveillance intelligente d'une salle intégrant :

- **7 capteurs** (DHT22, BH1750, PIR, ZMPT101B, ACS712, MQ-135, DS18B20)
- **Microcontrôleurs** STM32F4 + ESP32 avec FreeRTOS
- **Edge computing** sur Raspberry Pi 4 (MQTT + InfluxDB local)
- **Backend cloud** FastAPI + PostgreSQL + InfluxDB + Redis
- **IA/ML** : détection d'anomalies (IsolationForest + SVM) + prédiction consommation (XGBoost + Prophet)
- **Dashboard React** temps réel via WebSocket
- **Pipeline CI/CD** GitHub Actions → Docker → déploiement automatisé

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  HARDWARE LAYER                                                  │
│  STM32F4 (FreeRTOS) ──UART──► ESP32 ──MQTT/TLS──► RPi4         │
│  [7 capteurs + 4 actuateurs]   [WiFi + OTA]      [Edge]         │
└───────────────────────────────────────┬─────────────────────────┘
                                        │ HTTPS REST
┌───────────────────────────────────────▼─────────────────────────┐
│  BACKEND (FastAPI)                                               │
│  Auth · IoT · Analytics · Financial · ML · WebSocket            │
│  PostgreSQL · InfluxDB · Redis · RabbitMQ                        │
└───────────────────┬──────────────────────────────────────────────┘
                    │
        ┌───────────┴──────────┐
        ▼                      ▼
┌───────────────┐    ┌─────────────────────┐
│  ML SERVICE   │    │  FRONTEND (React)   │
│  IsolForest   │    │  Dashboard Temps    │
│  XGBoost      │    │  Réel + Analytics   │
│  Prophet      │    │  WebSocket + Charts │
│  MLflow       │    │                     │
└───────────────┘    └─────────────────────┘
```

---

## Prérequis

| Outil | Version minimum |
|-------|----------------|
| Docker + Docker Compose | 24.0 + 2.24 |
| Python | 3.11+ |
| Node.js | 18+ |
| Git | 2.40+ |

Pour le firmware :
- STM32CubeIDE **1.15+** ou PlatformIO
- PlatformIO Core (ESP32)
- Raspberry Pi OS Bookworm (64-bit)

---

## Démarrage rapide

### 1. Cloner le dépôt

```bash
git clone https://github.com/your-org/smart-room-system.git
cd smart-room-system
```

### 2. Configuration

```bash
cp .env.example .env
# Éditer .env avec vos valeurs
nano .env
```

### 3. Lancer la stack complète (développement)

```bash
cd infra
docker compose up -d

# Vérifier que tous les services sont up
docker compose ps
```

Services disponibles :

| Service | URL | Description |
|---------|-----|-------------|
| API Backend | http://localhost:8000/docs | Swagger UI |
| Frontend | http://localhost:3000 | Dashboard React |
| Grafana | http://localhost:3001 | Métriques (admin/admin) |
| MLflow | http://localhost:5000 | Expériences ML |
| Kibana | http://localhost:5601 | Logs |
| InfluxDB | http://localhost:8086 | Time series UI |

### 4. Initialiser la base de données

```bash
docker compose exec backend alembic upgrade head
```

---

## Hardware & Firmware

### Câblage STM32F4

```
PA0  → ZMPT101B (ADC)    PA4  → ACS712 (ADC)
PA9  → UART TX (ESP32)   PA10 → UART RX (ESP32)
PB6  → I2C SCL (BH1750)  PB7  → I2C SDA (BH1750)
PC0  → PIR HC-SR501      PC1  → DHT22
PC2  → DS18B20
PB0-3 → Relais 4 canaux  PB4  → Servo (TIM3 PWM)
PB5  → Buzzer
```

### Flash STM32

```bash
cd firmware/stm32
# Via STM32CubeIDE : Project > Build All, puis Run > Debug
```

### Flash ESP32

```bash
cd firmware/esp32
pio run --target upload --environment esp32dev
pio device monitor --baud 115200
```

### Setup Raspberry Pi

```bash
cd firmware/raspberry_pi/edge_processing
pip install -r requirements.txt
python edge_processor.py
```

---

## Backend

### Structure

```
backend/app/
├── api/v1/routes.py      # Tous les endpoints REST
├── core/
│   ├── config.py         # Settings Pydantic
│   └── security.py       # JWT + OAuth2
├── models/models.py      # SQLAlchemy ORM
├── schemas/schemas.py    # Pydantic v2 schemas
└── services/
    └── financial_service.py
```

### Lancer en local

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Endpoints principaux

```
POST /api/v1/auth/login          → JWT token pair
GET  /api/v1/rooms/              → Liste des salles
GET  /api/v1/sensors/{id}/latest → Dernières mesures
GET  /api/v1/financial/{id}/summary → Résumé financier
POST /api/v1/ml/{id}/train       → Lancer l'entraînement
WS   /ws/rooms/{id}              → WebSocket temps réel
```

---

## ML Service

### Modèles implémentés

| Modèle | Tâche | Librairie |
|--------|-------|-----------|
| IsolationForest | Détection anomalies | scikit-learn |
| OneClassSVM | Baseline anomalies | scikit-learn |
| XGBoost | Prédiction énergie | xgboost |
| Prophet | Tendances saisonnières | prophet |

### Entraîner les modèles

```bash
cd ml_service
pip install -r requirements.txt

# Entraîner pour une salle spécifique
python training/ml_pipeline.py --room-id <UUID> --days 30
```

---

## Frontend

```bash
cd frontend
npm install
npm run dev       # Développement
npm run build     # Production
npm run lint      # ESLint + TypeScript check
```

Pages :
- `/dashboard` — KPIs temps réel + graphiques 24h
- `/analytics` — Historique + heatmaps
- `/financial` — Coûts, budgets, prévisions
- `/ai-insights` — Anomalies + recommandations
- `/devices` — Contrôle actuateurs

---

## DevOps

### Docker Compose (dev/staging)

```bash
# Build et démarrer
docker compose -f infra/docker-compose.yml up --build -d

# Voir les logs
docker compose logs -f backend

# Arrêter
docker compose down -v
```

### CI/CD Pipeline

Le pipeline GitHub Actions se déclenche sur :
- **Push** → `main` : deploy staging
- **Tag** `v*.*.*` → deploy production
- **PR** → lint + tests uniquement

Secrets GitHub requis :
```
STAGING_HOST, STAGING_SSH_KEY
PRODUCTION_HOST, PRODUCTION_SSH_KEY
SLACK_WEBHOOK_URL
```

---

## Tests

```bash
# Backend — tests unitaires
cd backend
pytest tests/ -v --cov=app --cov-report=html

# Backend — tests d'intégration (nécessite Docker)
pytest tests/ -v -m integration

# Frontend
cd frontend
npm test
npm run test:coverage
```

Objectif couverture : **≥ 80%**

---

## Monitoring

### Grafana Dashboards

Après démarrage, importer les dashboards :
1. Grafana → Import → Upload JSON
2. Fichiers : `infra/monitoring/dashboards/`

Dashboards disponibles :
- **IoT Overview** — métriques capteurs temps réel
- **System Health** — CPU, RAM, réseau des services
- **API Performance** — latence, taux d'erreur, throughput
- **ML Monitoring** — drift détection, performance modèles

### Alertes Prometheus

Règles configurées dans `infra/monitoring/alert_rules.yml` :
- API latence p95 > 200ms
- Service down depuis > 1min
- Consommation mémoire > 85%
- Erreurs MQTT > 10/min

---

## Structure du projet

```
smart-room-system/
├── firmware/              # STM32 + ESP32 + Raspberry Pi
├── backend/               # FastAPI + PostgreSQL
├── ml_service/            # Modèles ML + MLflow
├── frontend/              # React + TypeScript
├── infra/                 # Docker + Prometheus + Mosquitto
├── .github/workflows/     # CI/CD GitHub Actions
├── docs/                  # Architecture + API + Rapport
├── .env.example           # Template variables d'environnement
└── README.md
```

---

## Auteur

Projet académique — Master Génie Électrique & Systèmes Embarqués  
© 2024 — Tous droits réservés

---

*Documentation complète : `docs/rapport/` | API interactive : http://localhost:8000/docs*
