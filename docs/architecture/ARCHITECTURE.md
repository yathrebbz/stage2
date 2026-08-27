# Smart Room & Financial Monitoring System — Architecture Globale

## Vue d'ensemble

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     SMART ROOM & FINANCIAL MONITORING SYSTEM                    │
│                     Embedded IoT · AI Analytics · DevOps                        │
└─────────────────────────────────────────────────────────────────────────────────┘

╔══════════════════════╗    ╔══════════════════════╗    ╔══════════════════════╗
║   COUCHE HARDWARE    ║    ║  COUCHE EDGE (RPi4)  ║    ║  COUCHE CLOUD/BACK  ║
║                      ║    ║                      ║    ║                      ║
║  ┌──────────────┐   ║    ║  ┌────────────────┐  ║    ║  ┌────────────────┐ ║
║  │   STM32F4    │   ║    ║  │   Mosquitto    │  ║    ║  │   FastAPI      │ ║
║  │  (FreeRTOS)  │   ║    ║  │ MQTT Broker    │  ║    ║  │  Microservices │ ║
║  │              │   ║    ║  │   (TLS)        │  ║    ║  └────────┬───────┘ ║
║  │ - DHT22      │   ║    ║  └───────┬────────┘  ║    ║           │          ║
║  │ - BH1750     │   ║    ║          │            ║    ║  ┌────────▼───────┐ ║
║  │ - PIR        │UART║   ║  ┌───────▼────────┐  ║    ║  │  PostgreSQL    │ ║
║  │ - ZMPT101B   ├───╫───►║  │   InfluxDB     │  ║HTTPS║  │  InfluxDB Cloud│ ║
║  │ - ACS712     │   ║    ║  │   (local TS)   │  ╠════►║  │  Redis Cache  │ ║
║  │ - MQ-135     │   ║    ║  └───────┬────────┘  ║    ║  └────────┬───────┘ ║
║  │ - DS18B20    │   ║    ║          │            ║    ║           │          ║
║  └──────┬───────┘   ║    ║  ┌───────▼────────┐  ║    ║  ┌────────▼───────┐ ║
║         │ UART/SPI  ║    ║  │  FastAPI local │  ║    ║  │  ML Service    │ ║
║  ┌──────▼───────┐   ║    ║  │  + Redis cache │  ║    ║  │ (Anomaly/Pred) │ ║
║  │    ESP32     │   ║    ║  └───────┬────────┘  ║    ║  └────────┬───────┘ ║
║  │  (FreeRTOS)  │   ║    ║          │ Python     ║    ║           │          ║
║  │ WiFi + MQTT  ├───╫────►  Edge Preprocess    ║    ║  ┌────────▼───────┐ ║
║  │ OTA Updates  │   ║    ║  (Kalman, outliers)  ║    ║  │   RabbitMQ    │ ║
║  └──────────────┘   ║    ║                      ║    ║  │  Message Queue│ ║
║                      ║    ║                      ║    ║  └────────────────┘ ║
║  Actuateurs :        ║    ║                      ║    ║                      ║
║  - Relais 4ch        ║    ║                      ║    ║                      ║
║  - Servo moteur      ║    ║                      ║    ║                      ║
║  - Buzzer            ║    ║                      ║    ║                      ║
║  - OLED SSD1306      ║    ║                      ║    ║                      ║
╚══════════════════════╝    ╚══════════════════════╝    ╚══════════════════════╝
                                                                    │
                    ┌───────────────────────────────────────────────┘
                    │
                    ▼
╔══════════════════════════════════════════════════════════════════════════════╗
║                          COUCHE FRONTEND                                     ║
║                                                                              ║
║  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   ║
║  │  Dashboard   │  │  Analytics   │  │  Financial   │  │  AI Insights │   ║
║  │  Principal   │  │    Room      │  │  Dashboard   │  │  Prédictions │   ║
║  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   ║
║  React.js + TypeScript · Tailwind CSS · Socket.IO · Recharts + D3.js        ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

## Diagramme de Flux de Données (DFD)

```
[Capteurs Physiques]
        │ Signal analogique/numérique
        ▼
[STM32F4 — Acquisition & Filtrage]
  • Lecture ADC (DMA, 100ms)
  • Filtre Kalman
  • Moyenne mobile 5 points
  • Conversion unités physiques
        │ UART (115200 bps) / SPI
        ▼
[ESP32 — Gateway IoT]
  • Réception données STM32
  • Sérialisation JSON
  • Publication MQTT (QoS 1)
  • Gestion reconnexion WiFi
        │ MQTT/TLS (port 8883)
        ▼
[Raspberry Pi — Edge Computing]
  • Broker Mosquitto (auth TLS)
  • Persistance InfluxDB locale
  • Pre-processing Python :
    - Détection outliers (IQR)
    - Normalisation min-max
    - Agrégation 1min/5min/1h
  • Cache Redis (dernières valeurs)
  • API locale FastAPI
        │ HTTPS REST + WebSocket
        ▼
[Backend Cloud — FastAPI Microservices]
  ┌─────────────────────────────────┐
  │  IoT Data Service               │──► InfluxDB Cloud
  │  Auth Service (JWT/OAuth2)      │──► PostgreSQL
  │  Analytics Service              │──► Redis
  │  AI/ML Service                  │──► MLflow
  │  Financial Service              │──► PostgreSQL
  │  Notification Service           │──► SendGrid/Twilio
  └─────────────────────────────────┘
        │ WebSocket / REST API
        ▼
[Frontend React.js]
  • Dashboard temps réel
  • Graphiques historiques
  • Contrôle actuateurs
  • Rapports financiers
```

## Choix Technologiques — Justification

### STM32F4 vs ESP32
- **STM32F4** : Cœur Cortex-M4 @ 168MHz, FPU hardware → calculs DSP temps réel (Kalman), determinisme RTOS critique
- **ESP32** : WiFi/BLE intégré, coût réduit → passerelle IoT idéale, pas de contrainte temps réel dure

### MQTT vs HTTP direct
- MQTT : protocol pub/sub léger (overhead minimal), QoS garantis, idéal IoT faible bande passante
- TLS obligatoire : authentification certificats mutuels (mTLS) entre ESP32 et Mosquitto

### FastAPI vs Django/Flask
- Async natif (asyncio) → performance sous charge IoT (1000+ msg/s)
- Validation automatique Pydantic → sécurité données entrantes
- OpenAPI auto-générée → documentation vivante

### InfluxDB vs PostgreSQL pour timeseries
- InfluxDB : optimisé séries temporelles, compression 90%+ vs RDBMS, queries temporelles natives
- PostgreSQL : données relationnelles (users, configs, transactions financières)

### MLflow
- Tracking expériences reproductibles
- Versioning modèles ML
- Registry centralisé pour déploiement

## Sécurité — Architecture Zero Trust

```
Internet
   │ HTTPS/TLS 1.3
   ▼
[WAF / Nginx Reverse Proxy]
   │ Rate Limiting (100 req/min/IP)
   ▼
[JWT Validation Middleware]
   │ Token valide (15min expiry)
   ▼
[API Services]
   │ Service-to-service mTLS
   ▼
[Bases de Données]
   Chiffrement at-rest (AES-256)
```

## Flux Financier

```
[Données Consommation kWh]
        │
        ▼
[Financial Service]
  • Lecture tarif électricité (configurable €/kWh)
  • Calcul coût temps réel
  • Agrégation journalière/mensuelle
  • Budget vs Réel
        │
        ▼
[ML Financial Analyzer]
  • Prédiction facture fin de mois (XGBoost)
  • Détection dépenses anormales (IsolationForest)
  • Recommandations économies (règles + ML)
        │
        ▼
[Alertes] → Email/SMS si dépassement budget
[Dashboard] → Graphiques coûts, ROI, projections
```
