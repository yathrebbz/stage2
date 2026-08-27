#!/usr/bin/env python3
"""
edge_processor.py — Smart Room Raspberry Pi Edge Computing
==========================================================
Souscription MQTT, preprocessing données IoT, stockage InfluxDB,
publication vers backend cloud via HTTPS.

Architecture:
    ESP32 → MQTT(TLS) → [Ce module] → InfluxDB Local
                                     → Redis Cache
                                     → Backend Cloud HTTPS

Usage:
    python3 edge_processor.py --config config.yaml

Requirements:
    pip install paho-mqtt influxdb-client redis aiohttp numpy scipy pydantic
"""

import asyncio
import json
import logging
import time
import signal
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional
import argparse

import numpy as np
import paho.mqtt.client as mqtt
import redis.asyncio as aioredis
import aiohttp
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from scipy import stats
import yaml

# ─── Configuration du logging ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/var/log/smartroom/edge_processor.log"),
    ]
)
logger = logging.getLogger("edge_processor")


# ─── Dataclasses ───────────────────────────────────────────

@dataclass
class SensorReading:
    """Représentation d'une lecture capteur normalisée."""
    timestamp: datetime
    device_id: str
    room_id: str
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    luminosity_lux: Optional[float] = None
    presence: Optional[bool] = None
    voltage_rms: Optional[float] = None
    current_rms: Optional[float] = None
    power_watts: Optional[float] = None
    co2_ppm: Optional[float] = None
    surface_temp: Optional[float] = None
    comfort_index: Optional[float] = None
    anomaly_flags: int = 0
    wifi_rssi: Optional[int] = None
    quality_score: float = 1.0


@dataclass
class OutlierDetector:
    """Détection d'outliers par IQR (méthode Tukey)."""
    window_size: int = 50
    iqr_multiplier: float = 2.5
    _buffer: Deque[float] = field(default_factory=lambda: deque(maxlen=50))

    def update(self, value: float) -> tuple[float, bool]:
        """
        Ajoute une valeur et teste si c'est un outlier.

        Returns:
            (valeur_nettoyée, est_outlier)
        """
        is_outlier = False

        if len(self._buffer) >= 10:
            q1 = np.percentile(list(self._buffer), 25)
            q3 = np.percentile(list(self._buffer), 75)
            iqr = q3 - q1
            lower_bound = q1 - self.iqr_multiplier * iqr
            upper_bound = q3 + self.iqr_multiplier * iqr

            if value < lower_bound or value > upper_bound:
                is_outlier = True
                # Remplacer par médiane de la fenêtre
                value = float(np.median(list(self._buffer)))
                logger.warning(f"Outlier détecté: valeur originale hors [{lower_bound:.2f}, {upper_bound:.2f}]")

        self._buffer.append(value)
        return value, is_outlier


# ─── Classe principale ──────────────────────────────────────

class EdgeProcessor:
    """
    Processeur edge Raspberry Pi pour données IoT Smart Room.

    Responsabilités:
    - Souscription MQTT avec TLS
    - Validation et normalisation des données
    - Détection d'outliers en temps réel
    - Persistence InfluxDB locale
    - Cache Redis pour API locale
    - Forwarding vers backend cloud
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.room_id = config["room"]["id"]
        self.running = False

        # Détecteurs d'outliers par métrique
        self._outlier_detectors: Dict[str, OutlierDetector] = {
            "temperature":   OutlierDetector(iqr_multiplier=2.5),
            "humidity":      OutlierDetector(iqr_multiplier=2.0),
            "luminosity":    OutlierDetector(iqr_multiplier=3.0),
            "power_watts":   OutlierDetector(iqr_multiplier=2.5),
            "co2_ppm":       OutlierDetector(iqr_multiplier=3.0),
        }

        # Stats de traitement
        self._stats = {
            "messages_received": 0,
            "messages_processed": 0,
            "outliers_detected": 0,
            "influx_writes": 0,
            "cloud_forwards": 0,
            "errors": 0,
        }

        # Queue async pour traitement
        self._processing_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        # Clients (initialisés dans setup())
        self._mqtt_client: Optional[mqtt.Client] = None
        self._influx_write_api = None
        self._redis: Optional[aioredis.Redis] = None
        self._http_session: Optional[aiohttp.ClientSession] = None

    # ──────────────────────────────────────────────────────
    #  SETUP
    # ──────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Initialise tous les clients et connexions."""
        logger.info("Initialisation Edge Processor...")

        # InfluxDB local
        self._influx_client = InfluxDBClient(
            url=self.config["influxdb"]["url"],
            token=self.config["influxdb"]["token"],
            org=self.config["influxdb"]["org"],
        )
        self._influx_write_api = self._influx_client.write_api(write_options=SYNCHRONOUS)
        logger.info("InfluxDB: connecté")

        # Redis
        self._redis = await aioredis.from_url(
            self.config["redis"]["url"],
            encoding="utf-8",
            decode_responses=True,
        )
        await self._redis.ping()
        logger.info("Redis: connecté")

        # HTTP Session vers backend
        ssl_ctx = None  # Configurer SSL si nécessaire
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        self._http_session = aiohttp.ClientSession(
            connector=connector,
            headers={
                "Authorization": f"Bearer {self.config['backend']['api_key']}",
                "Content-Type": "application/json",
                "X-Device-ID": self.config["device"]["id"],
            },
            timeout=aiohttp.ClientTimeout(total=10),
        )
        logger.info("HTTP Session: créée")

        # MQTT
        self._setup_mqtt()
        logger.info("Setup complet")

    def _setup_mqtt(self) -> None:
        """Configure le client MQTT avec TLS."""
        self._mqtt_client = mqtt.Client(
            client_id=f"rpi_edge_{self.room_id}",
            protocol=mqtt.MQTTv5,
        )

        # TLS
        self._mqtt_client.tls_set(
            ca_certs=self.config["mqtt"]["ca_cert"],
            certfile=self.config["mqtt"]["client_cert"],
            keyfile=self.config["mqtt"]["client_key"],
        )
        self._mqtt_client.tls_insecure_set(False)

        # Auth
        self._mqtt_client.username_pw_set(
            self.config["mqtt"]["username"],
            self.config["mqtt"]["password"],
        )

        # Callbacks
        self._mqtt_client.on_connect    = self._on_mqtt_connect
        self._mqtt_client.on_disconnect = self._on_mqtt_disconnect
        self._mqtt_client.on_message    = self._on_mqtt_message

        # Connexion
        self._mqtt_client.connect(
            self.config["mqtt"]["broker"],
            self.config["mqtt"]["port"],
            keepalive=60,
        )
        self._mqtt_client.loop_start()

    # ──────────────────────────────────────────────────────
    #  CALLBACKS MQTT
    # ──────────────────────────────────────────────────────

    def _on_mqtt_connect(self, client, userdata, flags, rc, properties=None) -> None:
        """Callback connexion MQTT."""
        if rc == 0:
            logger.info("MQTT: connecté au broker")
            topic = f"smartroom/{self.room_id}/sensors"
            client.subscribe(topic, qos=1)
            logger.info(f"MQTT: souscrit à {topic}")
        else:
            logger.error(f"MQTT: connexion échouée, code={rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc, properties=None) -> None:
        """Callback déconnexion MQTT."""
        if rc != 0:
            logger.warning(f"MQTT: déconnexion inattendue (rc={rc}) - reconnexion auto")

    def _on_mqtt_message(self, client, userdata, message: mqtt.MQTTMessage) -> None:
        """Callback réception message MQTT — met en queue pour traitement async."""
        self._stats["messages_received"] += 1
        try:
            payload_str = message.payload.decode("utf-8")
            # Mise en queue non-bloquante (boucle async)
            asyncio.get_event_loop().call_soon_threadsafe(
                self._processing_queue.put_nowait,
                {"topic": message.topic, "payload": payload_str, "ts": time.time()}
            )
        except Exception as e:
            logger.error(f"Erreur mise en queue MQTT: {e}")
            self._stats["errors"] += 1

    # ──────────────────────────────────────────────────────
    #  TRAITEMENT PRINCIPAL
    # ──────────────────────────────────────────────────────

    async def _process_loop(self) -> None:
        """Boucle principale de traitement des messages."""
        logger.info("Boucle de traitement démarrée")
        while self.running:
            try:
                # Attente message avec timeout
                try:
                    msg = await asyncio.wait_for(
                        self._processing_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                await self._process_message(msg)
                self._processing_queue.task_done()

            except Exception as e:
                logger.error(f"Erreur traitement: {e}", exc_info=True)
                self._stats["errors"] += 1

    async def _process_message(self, msg: Dict[str, Any]) -> None:
        """
        Pipeline complet de traitement d'un message MQTT.

        1. Parse JSON
        2. Validation données
        3. Détection outliers
        4. Calcul score qualité
        5. Stockage InfluxDB
        6. Mise à jour cache Redis
        7. Forward vers backend cloud (batché)
        """
        try:
            raw = json.loads(msg["payload"])
        except json.JSONDecodeError as e:
            logger.error(f"JSON invalide: {e}")
            return

        # ── Parse et validation ──
        reading = self._parse_reading(raw, msg["ts"])
        if reading is None:
            return

        # ── Détection outliers ──
        reading = await self._apply_outlier_detection(reading)

        # ── Stockage InfluxDB ──
        await self._write_to_influx(reading)

        # ── Cache Redis ──
        await self._update_redis_cache(reading)

        # ── Forward backend ──
        await self._forward_to_backend(reading)

        self._stats["messages_processed"] += 1

    def _parse_reading(self, raw: Dict, timestamp_recv: float) -> Optional[SensorReading]:
        """Parse et valide un message brut STM32/ESP32."""
        try:
            # Timestamp depuis STM32 (ms depuis boot) ou timestamp réception
            ts = datetime.now(timezone.utc)

            reading = SensorReading(
                timestamp=ts,
                device_id=raw.get("device_id", "unknown"),
                room_id=self.room_id,
                temperature=self._validate_float(raw.get("t"), -40, 85),
                humidity=self._validate_float(raw.get("h"), 0, 100),
                luminosity_lux=self._validate_float(raw.get("l"), 0, 100000),
                presence=bool(raw.get("p", 0)),
                voltage_rms=self._validate_float(raw.get("v"), 0, 260),
                current_rms=self._validate_float(raw.get("i"), 0, 30),
                power_watts=self._validate_float(raw.get("w"), 0, 10000),
                co2_ppm=self._validate_float(raw.get("co2"), 400, 10000),
                surface_temp=self._validate_float(raw.get("st"), -40, 85),
                comfort_index=self._validate_float(raw.get("ci"), 0, 100),
                anomaly_flags=int(raw.get("af", 0)),
                wifi_rssi=raw.get("wifi_rssi"),
            )

            # Score qualité basé sur validité des champs
            valid_fields = sum([
                reading.temperature is not None,
                reading.humidity is not None,
                reading.luminosity_lux is not None,
                reading.power_watts is not None,
            ])
            reading.quality_score = valid_fields / 4.0

            return reading

        except Exception as e:
            logger.error(f"Erreur parsing: {e}, raw={raw}")
            return None

    @staticmethod
    def _validate_float(value: Any, min_val: float, max_val: float) -> Optional[float]:
        """Valide et retourne un float dans une plage physique."""
        if value is None:
            return None
        try:
            f = float(value)
            return f if min_val <= f <= max_val else None
        except (ValueError, TypeError):
            return None

    async def _apply_outlier_detection(self, reading: SensorReading) -> SensorReading:
        """Applique la détection d'outliers IQR sur chaque métrique."""
        metrics = {
            "temperature":  ("temperature", reading.temperature),
            "humidity":     ("humidity", reading.humidity),
            "luminosity":   ("luminosity_lux", reading.luminosity_lux),
            "power_watts":  ("power_watts", reading.power_watts),
            "co2_ppm":      ("co2_ppm", reading.co2_ppm),
        }

        for key, (attr, value) in metrics.items():
            if value is not None:
                cleaned, is_outlier = self._outlier_detectors[key].update(value)
                setattr(reading, attr, cleaned)
                if is_outlier:
                    self._stats["outliers_detected"] += 1
                    reading.quality_score *= 0.7  # Réduction qualité

        return reading

    async def _write_to_influx(self, reading: SensorReading) -> None:
        """Écrit les données dans InfluxDB local."""
        try:
            points = []
            bucket = self.config["influxdb"]["bucket"]
            org = self.config["influxdb"]["org"]

            # Point principal
            p = (
                Point("sensor_data")
                .tag("room_id", reading.room_id)
                .tag("device_id", reading.device_id)
                .field("quality_score", reading.quality_score)
                .field("anomaly_flags", reading.anomaly_flags)
                .time(reading.timestamp, WritePrecision.NANOSECONDS)
            )

            # Ajout des métriques non-nulles
            metric_map = {
                "temperature": reading.temperature,
                "humidity": reading.humidity,
                "luminosity_lux": reading.luminosity_lux,
                "presence": int(reading.presence) if reading.presence is not None else None,
                "voltage_rms": reading.voltage_rms,
                "current_rms": reading.current_rms,
                "power_watts": reading.power_watts,
                "co2_ppm": reading.co2_ppm,
                "surface_temp": reading.surface_temp,
                "comfort_index": reading.comfort_index,
            }

            for field_name, value in metric_map.items():
                if value is not None:
                    p = p.field(field_name, float(value))

            points.append(p)

            self._influx_write_api.write(bucket=bucket, org=org, record=points)
            self._stats["influx_writes"] += 1

        except Exception as e:
            logger.error(f"Erreur InfluxDB: {e}")
            self._stats["errors"] += 1

    async def _update_redis_cache(self, reading: SensorReading) -> None:
        """Met à jour le cache Redis avec les dernières valeurs."""
        try:
            cache_key = f"room:{self.room_id}:latest"
            cache_data = {
                "timestamp": reading.timestamp.isoformat(),
                "temperature": reading.temperature,
                "humidity": reading.humidity,
                "luminosity_lux": reading.luminosity_lux,
                "presence": reading.presence,
                "power_watts": reading.power_watts,
                "co2_ppm": reading.co2_ppm,
                "comfort_index": reading.comfort_index,
                "quality_score": reading.quality_score,
            }
            # TTL 5 minutes
            await self._redis.setex(
                cache_key,
                300,
                json.dumps({k: v for k, v in cache_data.items() if v is not None})
            )

            # Pub/Sub pour SSE
            await self._redis.publish(
                f"room:{self.room_id}:updates",
                json.dumps(cache_data)
            )

        except Exception as e:
            logger.error(f"Erreur Redis: {e}")

    async def _forward_to_backend(self, reading: SensorReading) -> None:
        """Forward les données vers le backend cloud via HTTPS."""
        try:
            payload = {
                "device_id": reading.device_id,
                "room_id": reading.room_id,
                "timestamp": reading.timestamp.isoformat(),
                "readings": [
                    {"sensor_type": k, "value": v, "unit": self._get_unit(k)}
                    for k, v in {
                        "temperature": reading.temperature,
                        "humidity": reading.humidity,
                        "luminosity": reading.luminosity_lux,
                        "power": reading.power_watts,
                        "co2": reading.co2_ppm,
                    }.items()
                    if v is not None
                ],
                "quality_score": reading.quality_score,
                "anomaly_flags": reading.anomaly_flags,
            }

            backend_url = f"{self.config['backend']['url']}/api/v1/devices/{reading.device_id}/data"
            async with self._http_session.post(backend_url, json=payload) as resp:
                if resp.status in (200, 202):
                    self._stats["cloud_forwards"] += 1
                else:
                    body = await resp.text()
                    logger.warning(f"Backend réponse {resp.status}: {body[:200]}")

        except aiohttp.ClientError as e:
            logger.error(f"Erreur HTTP backend: {e}")
        except Exception as e:
            logger.error(f"Erreur forward: {e}")

    @staticmethod
    def _get_unit(sensor_type: str) -> str:
        """Retourne l'unité pour un type de capteur."""
        units = {
            "temperature": "°C",
            "humidity": "%",
            "luminosity": "lux",
            "power": "W",
            "co2": "ppm",
            "current": "A",
            "voltage": "V",
        }
        return units.get(sensor_type, "")

    # ──────────────────────────────────────────────────────
    #  RUN / STOP
    # ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """Lance le processeur edge."""
        await self.setup()
        self.running = True
        logger.info("Edge Processor démarré")

        # Tâches concurrentes
        tasks = [
            asyncio.create_task(self._process_loop()),
            asyncio.create_task(self._stats_reporter()),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Arrêt demandé")
        finally:
            await self.shutdown()

    async def _stats_reporter(self) -> None:
        """Rapporte les statistiques toutes les 60 secondes."""
        while self.running:
            await asyncio.sleep(60)
            logger.info(
                f"STATS: recv={self._stats['messages_received']} "
                f"proc={self._stats['messages_processed']} "
                f"outliers={self._stats['outliers_detected']} "
                f"influx={self._stats['influx_writes']} "
                f"cloud={self._stats['cloud_forwards']} "
                f"errors={self._stats['errors']}"
            )

    async def shutdown(self) -> None:
        """Arrêt propre."""
        self.running = False
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()
        if self._redis:
            await self._redis.close()
        if self._http_session:
            await self._http_session.close()
        if hasattr(self, "_influx_client"):
            self._influx_client.close()
        logger.info("Edge Processor arrêté proprement")


# ─── Entrypoint ────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Room Edge Processor")
    parser.add_argument("--config", default="config.yaml", help="Fichier de configuration YAML")
    args = parser.parse_args()

    with open(args.config, "r") as f:
        config = yaml.safe_load(f)

    processor = EdgeProcessor(config)

    # Gestion arrêt propre
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(processor.shutdown()))

    try:
        loop.run_until_complete(processor.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
