/**
 * @file    main.cpp
 * @brief   Firmware ESP32 — Gateway IoT Smart Room
 * @details WiFi Manager, MQTT/TLS, OTA Updates, Deep Sleep, Watchdog
 *
 * Fonctionnalités :
 *  - Réception données STM32 via UART2
 *  - Publication MQTT vers Raspberry Pi (TLS, QoS 1)
 *  - Souscription MQTT pour commandes actuateurs
 *  - OTA Updates via HTTP
 *  - Deep sleep si absence prolongée
 *  - Watchdog Timer 30s
 *  - Reconnexion automatique WiFi/MQTT
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <HTTPUpdate.h>
#include <EEPROM.h>
#include <esp_task_wdt.h>
#include <esp_sleep.h>

/* ─── Configuration (remplacé par NVS en prod) ─── */
#define WIFI_SSID           "SmartRoom_Network"
#define WIFI_PASSWORD       "SecurePass2024!"
#define MQTT_BROKER         "192.168.1.100"
#define MQTT_PORT           8883
#define MQTT_USERNAME       "esp32_gateway"
#define MQTT_PASSWORD       "mqtt_secure_pass"
#define MQTT_CLIENT_ID      "esp32_room_001"
#define OTA_SERVER_URL      "https://ota.smartroom.local/firmware/esp32_latest.bin"
#define FIRMWARE_VERSION    "1.2.0"

/* ─── Topics MQTT ─── */
#define TOPIC_SENSOR_DATA   "smartroom/room001/sensors"
#define TOPIC_ACTUATOR_CMD  "smartroom/room001/commands"
#define TOPIC_DEVICE_STATUS "smartroom/room001/status"
#define TOPIC_OTA_TRIGGER   "smartroom/room001/ota"
#define TOPIC_HEARTBEAT     "smartroom/heartbeat"

/* ─── Pins ─── */
#define UART_RX_PIN         16   /* UART2 RX depuis STM32 */
#define UART_TX_PIN         17   /* UART2 TX vers STM32 */
#define LED_STATUS_PIN      2    /* LED built-in statut */
#define DEEP_SLEEP_WAKEUP   GPIO_NUM_34 /* PIR wakeup */

/* ─── Timings ─── */
#define WIFI_TIMEOUT_MS         15000
#define MQTT_RECONNECT_DELAY_MS 5000
#define HEARTBEAT_INTERVAL_MS   30000
#define OTA_CHECK_INTERVAL_MS   3600000 /* 1h */
#define WDT_TIMEOUT_S           30
#define DEEP_SLEEP_DELAY_MIN    10  /* Absence > 10min → deep sleep */

/* ─── Certificat CA Mosquitto (auto-signé) ─── */
const char* ca_cert = R"EOF(
-----BEGIN CERTIFICATE-----
MIIDazCCAlOgAwIBAgIUYourCertificateHere...
(Insérer votre certificat CA Mosquitto ici)
-----END CERTIFICATE-----
)EOF";

/* ─── Objets globaux ─── */
WiFiClientSecure wifiClient;
PubSubClient mqttClient(wifiClient);
HardwareSerial stm32Serial(2); /* UART2 */

/* ─── Variables d'état ─── */
volatile bool uart_data_ready = false;
char uart_buffer[512];
uint16_t uart_buffer_idx = 0;
uint32_t last_heartbeat_ms    = 0;
uint32_t last_ota_check_ms    = 0;
uint32_t last_presence_ms     = 0;
uint32_t mqtt_reconnect_count = 0;
bool presence_detected        = false;

/* ─── Prototypes ─── */
bool  WiFi_Connect(void);
bool  MQTT_Connect(void);
void  MQTT_Callback(char* topic, byte* payload, unsigned int length);
void  Process_STM32_Data(const char* json_str);
void  Publish_SensorData(const JsonDocument& doc);
void  Publish_DeviceStatus(void);
void  Handle_OTA_Update(void);
void  Check_DeepSleep(void);
void  LED_Blink(int times, int delay_ms);
void  Task_MQTT_Loop(void* pvParameters);
void  Task_UART_Read(void* pvParameters);
void  Task_Watchdog_Feed(void* pvParameters);

/* ══════════════════════════════════════════════════════════
 *  SETUP
 * ══════════════════════════════════════════════════════════ */
void setup()
{
    Serial.begin(115200);
    Serial.printf("\n[BOOT] Smart Room ESP32 Gateway v%s\n", FIRMWARE_VERSION);

    /* Initialisation UART vers STM32 */
    stm32Serial.begin(115200, SERIAL_8N1, UART_RX_PIN, UART_TX_PIN);

    /* GPIO */
    pinMode(LED_STATUS_PIN, OUTPUT);

    /* Watchdog Timer */
    esp_task_wdt_init(WDT_TIMEOUT_S, true);
    esp_task_wdt_add(NULL);

    /* Configuration TLS */
    wifiClient.setCACert(ca_cert);

    /* Connexion WiFi */
    if (!WiFi_Connect()) {
        Serial.println("[ERROR] WiFi connection failed - restarting in 10s");
        delay(10000);
        ESP.restart();
    }

    /* Configuration MQTT */
    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    mqttClient.setCallback(MQTT_Callback);
    mqttClient.setBufferSize(1024);
    mqttClient.setKeepAlive(60);

    /* Connexion MQTT */
    MQTT_Connect();

    /* Vérification OTA au démarrage */
    Handle_OTA_Update();

    /* Création tâches FreeRTOS */
    xTaskCreatePinnedToCore(Task_UART_Read,      "uart_read",  4096, NULL, 5, NULL, 0);
    xTaskCreatePinnedToCore(Task_MQTT_Loop,      "mqtt_loop",  8192, NULL, 4, NULL, 1);
    xTaskCreatePinnedToCore(Task_Watchdog_Feed,  "wdt_feed",   2048, NULL, 1, NULL, 0);

    Serial.println("[BOOT] All tasks created - System running");
    LED_Blink(3, 200);
}

void loop()
{
    /* Heartbeat + monitoring */
    uint32_t now = millis();

    if (now - last_heartbeat_ms > HEARTBEAT_INTERVAL_MS) {
        Publish_DeviceStatus();
        last_heartbeat_ms = now;
    }

    if (now - last_ota_check_ms > OTA_CHECK_INTERVAL_MS) {
        Handle_OTA_Update();
        last_ota_check_ms = now;
    }

    /* Deep sleep si pas de présence prolongée */
    Check_DeepSleep();

    esp_task_wdt_reset();
    vTaskDelay(pdMS_TO_TICKS(1000));
}

/* ══════════════════════════════════════════════════════════
 *  TÂCHE : Lecture UART STM32
 * ══════════════════════════════════════════════════════════ */
void Task_UART_Read(void* pvParameters)
{
    for (;;) {
        while (stm32Serial.available()) {
            char c = stm32Serial.read();
            if (c == '\n') {
                uart_buffer[uart_buffer_idx] = '\0';
                if (uart_buffer_idx > 10) {  /* Données suffisantes */
                    Process_STM32_Data(uart_buffer);
                }
                uart_buffer_idx = 0;
            } else if (uart_buffer_idx < sizeof(uart_buffer) - 1) {
                uart_buffer[uart_buffer_idx++] = c;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

/* ══════════════════════════════════════════════════════════
 *  TÂCHE : Boucle MQTT
 * ══════════════════════════════════════════════════════════ */
void Task_MQTT_Loop(void* pvParameters)
{
    for (;;) {
        if (!mqttClient.connected()) {
            Serial.printf("[MQTT] Disconnected (count: %lu) - reconnecting...\n",
                          ++mqtt_reconnect_count);
            if (!WiFi.isConnected()) {
                WiFi_Connect();
            }
            MQTT_Connect();
        }
        mqttClient.loop();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

/* ══════════════════════════════════════════════════════════
 *  TÂCHE : Alimentation watchdog
 * ══════════════════════════════════════════════════════════ */
void Task_Watchdog_Feed(void* pvParameters)
{
    for (;;) {
        esp_task_wdt_reset();
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

/* ══════════════════════════════════════════════════════════
 *  CONNEXION WiFi
 * ══════════════════════════════════════════════════════════ */
bool WiFi_Connect(void)
{
    Serial.printf("[WiFi] Connecting to %s", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        if (millis() - start > WIFI_TIMEOUT_MS) {
            Serial.println(" TIMEOUT");
            return false;
        }
        Serial.print(".");
        delay(500);
    }

    Serial.printf(" OK\n[WiFi] IP: %s, RSSI: %d dBm\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
    return true;
}

/* ══════════════════════════════════════════════════════════
 *  CONNEXION MQTT
 * ══════════════════════════════════════════════════════════ */
bool MQTT_Connect(void)
{
    Serial.printf("[MQTT] Connecting to %s:%d\n", MQTT_BROKER, MQTT_PORT);

    /* Will message pour détection déconnexion inattendue */
    const char* will_topic   = TOPIC_DEVICE_STATUS;
    const char* will_payload = "{\"online\":false,\"reason\":\"unexpected_disconnect\"}";

    if (mqttClient.connect(MQTT_CLIENT_ID, MQTT_USERNAME, MQTT_PASSWORD,
                           will_topic, 1, true, will_payload)) {
        Serial.println("[MQTT] Connected");

        /* Souscriptions */
        mqttClient.subscribe(TOPIC_ACTUATOR_CMD, 1);
        mqttClient.subscribe(TOPIC_OTA_TRIGGER,  1);

        /* Annonce présence */
        char status_json[256];
        snprintf(status_json, sizeof(status_json),
            "{\"online\":true,\"version\":\"%s\",\"ip\":\"%s\","
            "\"rssi\":%d,\"free_heap\":%lu}",
            FIRMWARE_VERSION,
            WiFi.localIP().toString().c_str(),
            WiFi.RSSI(),
            (unsigned long)esp_get_free_heap_size());

        mqttClient.publish(TOPIC_DEVICE_STATUS, status_json, true);
        return true;
    }

    Serial.printf("[MQTT] Failed, rc=%d\n", mqttClient.state());
    return false;
}

/* ══════════════════════════════════════════════════════════
 *  CALLBACK MQTT — Commandes reçues
 * ══════════════════════════════════════════════════════════ */
void MQTT_Callback(char* topic, byte* payload, unsigned int length)
{
    /* Null-terminate */
    char msg[512];
    uint16_t len = min(length, (unsigned int)(sizeof(msg) - 1));
    memcpy(msg, payload, len);
    msg[len] = '\0';

    Serial.printf("[MQTT] Received on %s: %s\n", topic, msg);

    if (strcmp(topic, TOPIC_ACTUATOR_CMD) == 0) {
        /* Parser la commande et transmettre à STM32 via UART */
        StaticJsonDocument<256> cmd_doc;
        if (deserializeJson(cmd_doc, msg) == DeserializationError::Ok) {
            /* Format: {"actuator": 0, "cmd": 1, "duration": 0} */
            char stm32_cmd[64];
            snprintf(stm32_cmd, sizeof(stm32_cmd),
                "CMD:%d:%d:%d\n",
                (int)cmd_doc["actuator"],
                (int)cmd_doc["cmd"],
                (int)cmd_doc["duration"]);
            stm32Serial.print(stm32_cmd);
            Serial.printf("[UART→STM32] %s", stm32_cmd);
        }

    } else if (strcmp(topic, TOPIC_OTA_TRIGGER) == 0) {
        Serial.println("[OTA] Update triggered via MQTT");
        Handle_OTA_Update();
    }
}

/* ══════════════════════════════════════════════════════════
 *  TRAITEMENT DONNÉES STM32
 * ══════════════════════════════════════════════════════════ */
void Process_STM32_Data(const char* json_str)
{
    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, json_str);

    if (err != DeserializationError::Ok) {
        Serial.printf("[UART] JSON parse error: %s\n", err.c_str());
        return;
    }

    /* Suivi présence pour deep sleep */
    if (doc.containsKey("p")) {
        presence_detected = (bool)doc["p"];
        if (presence_detected) last_presence_ms = millis();
    }

    /* Ajout métadonnées ESP32 */
    doc["device_id"] = MQTT_CLIENT_ID;
    doc["wifi_rssi"] = WiFi.RSSI();
    doc["fw_version"] = FIRMWARE_VERSION;

    /* Publication MQTT */
    Publish_SensorData(doc);
}

/* ══════════════════════════════════════════════════════════
 *  PUBLICATION MQTT DONNÉES CAPTEURS
 * ══════════════════════════════════════════════════════════ */
void Publish_SensorData(const JsonDocument& doc)
{
    char output[768];
    size_t len = serializeJson(doc, output, sizeof(output));

    if (len > 0) {
        bool success = mqttClient.publish(TOPIC_SENSOR_DATA, output, false);
        if (!success) {
            Serial.println("[MQTT] Publish FAILED");
        }
    }
}

/* ══════════════════════════════════════════════════════════
 *  PUBLICATION STATUS DEVICE
 * ══════════════════════════════════════════════════════════ */
void Publish_DeviceStatus(void)
{
    StaticJsonDocument<256> status;
    status["online"]     = true;
    status["version"]    = FIRMWARE_VERSION;
    status["uptime_s"]   = millis() / 1000;
    status["free_heap"]  = esp_get_free_heap_size();
    status["wifi_rssi"]  = WiFi.RSSI();
    status["mqtt_reconnects"] = mqtt_reconnect_count;

    char output[256];
    serializeJson(status, output, sizeof(output));
    mqttClient.publish(TOPIC_DEVICE_STATUS, output, true);
}

/* ══════════════════════════════════════════════════════════
 *  OTA UPDATE
 * ══════════════════════════════════════════════════════════ */
void Handle_OTA_Update(void)
{
    Serial.println("[OTA] Checking for updates...");

    WiFiClientSecure ota_client;
    ota_client.setCACert(ca_cert);

    httpUpdate.setLedPin(LED_STATUS_PIN, LOW);
    httpUpdate.onStart([]() { Serial.println("[OTA] Update started"); });
    httpUpdate.onEnd([]() { Serial.println("[OTA] Update finished"); });
    httpUpdate.onError([](int err) {
        Serial.printf("[OTA] Error: %d\n", err);
    });
    httpUpdate.onProgress([](int cur, int total) {
        Serial.printf("[OTA] Progress: %d/%d bytes\n", cur, total);
    });

    t_httpUpdate_return ret = httpUpdate.update(ota_client, OTA_SERVER_URL);

    switch (ret) {
        case HTTP_UPDATE_FAILED:
            Serial.printf("[OTA] Failed: %s\n", httpUpdate.getLastErrorString().c_str());
            break;
        case HTTP_UPDATE_NO_UPDATES:
            Serial.println("[OTA] Already up to date");
            break;
        case HTTP_UPDATE_OK:
            Serial.println("[OTA] Update OK - rebooting");
            break;
    }
}

/* ══════════════════════════════════════════════════════════
 *  GESTION DEEP SLEEP
 * ══════════════════════════════════════════════════════════ */
void Check_DeepSleep(void)
{
    uint32_t absence_ms = millis() - last_presence_ms;
    if (absence_ms > (uint32_t)DEEP_SLEEP_DELAY_MIN * 60 * 1000) {
        Serial.printf("[SLEEP] No presence for %lu min - entering deep sleep\n",
                      absence_ms / 60000);

        /* Publier état offline */
        mqttClient.publish(TOPIC_DEVICE_STATUS,
            "{\"online\":false,\"reason\":\"deep_sleep\"}", true);
        mqttClient.loop();
        delay(100);

        /* Configuration réveil sur GPIO (PIR) */
        esp_sleep_enable_ext0_wakeup(DEEP_SLEEP_WAKEUP, 1);

        Serial.println("[SLEEP] Entering deep sleep...");
        esp_deep_sleep_start();
    }
}

/* ─── Utilitaire LED ─── */
void LED_Blink(int times, int delay_ms)
{
    for (int i = 0; i < times; i++) {
        digitalWrite(LED_STATUS_PIN, HIGH);
        delay(delay_ms);
        digitalWrite(LED_STATUS_PIN, LOW);
        delay(delay_ms);
    }
}
