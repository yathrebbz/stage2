/**
 * @file    sensor_tasks.c
 * @brief   Implémentation des tâches FreeRTOS — Smart Room STM32F4
 * @details Acquisition capteurs, filtrage Kalman, contrôle PID, comm UART
 *
 * Pinout STM32F4 :
 *   PA0  → ADC1_CH0 : ZMPT101B (tension)
 *   PA1  → ADC1_CH1 : ACS712 (courant)
 *   PA4  → ADC1_CH4 : MQ-135 (qualité air)
 *   PB6  → I2C1_SCL : BH1750
 *   PB7  → I2C1_SDA : BH1750
 *   PC0  → GPIO_IN  : PIR HC-SR501
 *   PC1  → 1-Wire   : DHT22
 *   PC2  → 1-Wire   : DS18B20
 *   PA9  → USART1_TX: vers ESP32
 *   PA10 → USART1_RX: depuis ESP32
 *   PB0..PB3 → GPIO_OUT : Relais 4 canaux
 *   PB4  → TIM3_CH1 : Servo PWM
 *   PB5  → GPIO_OUT : Buzzer
 */

#include "sensor_tasks.h"
#include "dht22.h"
#include "bh1750.h"
#include "ds18b20.h"
#include <string.h>
#include <stdio.h>
#include <math.h>

/* ─── Handles globaux ─── */
QueueHandle_t    xSensorRawQueue;
QueueHandle_t    xSensorProcessedQueue;
QueueHandle_t    xActuatorCmdQueue;
SemaphoreHandle_t xI2C_Mutex;
SemaphoreHandle_t xUART_Mutex;

/* ─── Variables statiques de filtrage ─── */
static KalmanFilter_t kf_temperature;
static KalmanFilter_t kf_humidity;
static KalmanFilter_t kf_surface_temp;
static KalmanFilter_t kf_current;

static float temp_avg_buffer[MOVING_AVG_WINDOW]   = {0};
static float humid_avg_buffer[MOVING_AVG_WINDOW]  = {0};
static uint8_t temp_avg_idx  = 0;
static uint8_t humid_avg_idx = 0;

/* ─── Variables anti-rebond présence ─── */
static uint32_t presence_last_trigger = 0;
#define PRESENCE_DEBOUNCE_MS    500U

/* ─── Handles périphériques (initialisés dans main.c) ─── */
extern ADC_HandleTypeDef hadc1;
extern I2C_HandleTypeDef hi2c1;
extern UART_HandleTypeDef huart1;
extern TIM_HandleTypeDef htim3;

/* ─── DMA buffer ADC ─── */
static uint16_t adc_dma_buffer[ADC_CHANNELS_COUNT * 16]; /* 16 samples/channel oversampling */

/* ══════════════════════════════════════════════════════════
 *  TÂCHE 1 : Acquisition capteurs (Priorité 4 - Haute)
 * ══════════════════════════════════════════════════════════ */
void Task_SensorAcquisition(void *pvParameters)
{
    (void)pvParameters;
    SensorRawData_t raw_data;
    TickType_t xLastWakeTime = xTaskGetTickCount();
    const TickType_t xPeriod = pdMS_TO_TICKS(SENSOR_TASK_PERIOD_MS);

    /* Démarrage DMA ADC en mode circulaire */
    HAL_ADC_Start_DMA(&hadc1, (uint32_t*)adc_dma_buffer,
                      ADC_CHANNELS_COUNT * 16);

    for (;;) {
        uint32_t tick_start = HAL_GetTick();
        raw_data.data_valid = 0x00;

        /* ── Lecture DHT22 (Temperature + Humidité) ── */
        DHT22_Data_t dht_data;
        if (DHT22_Read(&dht_data) == DHT22_OK) {
            raw_data.temperature = dht_data.temperature;
            raw_data.humidity    = dht_data.humidity;
            raw_data.data_valid |= (1 << 0);
        } else {
            raw_data.temperature = -999.0f; /* valeur invalide */
        }

        /* ── Lecture BH1750 (Luminosité) via I2C ── */
        if (xSemaphoreTake(xI2C_Mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            uint16_t lux;
            if (BH1750_ReadLux(&hi2c1, &lux) == HAL_OK) {
                raw_data.luminosity_raw = lux;
                raw_data.data_valid |= (1 << 1);
            }
            xSemaphoreGive(xI2C_Mutex);
        }

        /* ── Lecture PIR HC-SR501 (Présence) ── */
        raw_data.presence = HAL_GPIO_ReadPin(GPIOC, GPIO_PIN_0);
        raw_data.data_valid |= (1 << 2);

        /* ── Lecture ADC via DMA buffer (oversampled) ── */
        /* Canal 0: ZMPT101B - calcul Vrms */
        float v_rms = 0.0f;
        for (int i = 0; i < 16; i++) {
            float v = ((float)adc_dma_buffer[i * ADC_CHANNELS_COUNT + 0] / ADC_RESOLUTION) * VREF_MV;
            v -= (VREF_MV / 2.0f); /* Centrage */
            v_rms += v * v;
        }
        raw_data.voltage_rms = sqrtf(v_rms / 16.0f) * (230.0f / 165.0f); /* Echelle vers 230V AC */
        raw_data.data_valid |= (1 << 3);

        /* Canal 1: ACS712 - calcul courant */
        float i_rms = 0.0f;
        for (int i = 0; i < 16; i++) {
            float adc_v = ((float)adc_dma_buffer[i * ADC_CHANNELS_COUNT + 1] / ADC_RESOLUTION) * VREF_MV;
            float current_ma = (adc_v - 1500.0f) / 66.0f; /* ACS712-5A: 66mV/A, offset 1500mV */
            i_rms += current_ma * current_ma;
        }
        raw_data.current_rms = sqrtf(i_rms / 16.0f) / 1000.0f; /* mA → A */
        raw_data.data_valid |= (1 << 4);

        /* Canal 2: MQ-135 */
        uint32_t mq135_sum = 0;
        for (int i = 0; i < 16; i++) {
            mq135_sum += adc_dma_buffer[i * ADC_CHANNELS_COUNT + 2];
        }
        raw_data.air_quality_raw = (uint16_t)(mq135_sum / 16);
        raw_data.data_valid |= (1 << 5);

        /* ── Lecture DS18B20 (Température surface) ── */
        float ds_temp;
        if (DS18B20_Read(&ds_temp) == DS18B20_OK) {
            raw_data.surface_temp = ds_temp;
            raw_data.data_valid |= (1 << 6);
        }

        raw_data.timestamp_ms = tick_start;

        /* Envoi vers queue de traitement (sans blocage) */
        if (xQueueSend(xSensorRawQueue, &raw_data, 0) != pdPASS) {
            /* Queue pleine - log overflow, données ignorées */
        }

        vTaskDelayUntil(&xLastWakeTime, xPeriod);
    }
}

/* ══════════════════════════════════════════════════════════
 *  TÂCHE 2 : Traitement signal & filtrage (Priorité 3)
 * ══════════════════════════════════════════════════════════ */
void Task_DataProcessing(void *pvParameters)
{
    (void)pvParameters;
    SensorRawData_t    raw;
    SensorProcessedData_t processed;

    /* Init filtres Kalman */
    Kalman_Init(&kf_temperature,  22.0f, 0.5f, 0.1f);
    Kalman_Init(&kf_humidity,     50.0f, 1.0f, 0.2f);
    Kalman_Init(&kf_surface_temp, 22.0f, 0.3f, 0.05f);
    Kalman_Init(&kf_current,       0.0f, 0.1f, 0.01f);

    for (;;) {
        /* Attente données brutes */
        if (xQueueReceive(xSensorRawQueue, &raw, portMAX_DELAY) != pdPASS)
            continue;

        processed.timestamp_ms = raw.timestamp_ms;

        /* ── Filtrage température ── */
        if (raw.data_valid & (1 << 0)) {
            float kalman_temp = Kalman_Update(&kf_temperature, raw.temperature);
            processed.temperature_filtered = MovingAverage_Update(
                temp_avg_buffer, &temp_avg_idx, kalman_temp, MOVING_AVG_WINDOW);
        }

        /* ── Filtrage humidité ── */
        if (raw.data_valid & (1 << 0)) {
            float kalman_hum = Kalman_Update(&kf_humidity, raw.humidity);
            processed.humidity_filtered = MovingAverage_Update(
                humid_avg_buffer, &humid_avg_idx, kalman_hum, MOVING_AVG_WINDOW);
        }

        /* ── Luminosité → Lux (BH1750 déjà en lux) ── */
        processed.luminosity_lux = (float)raw.luminosity_raw;

        /* ── Présence anti-rebond ── */
        uint32_t now = HAL_GetTick();
        if (raw.presence && (now - presence_last_trigger > PRESENCE_DEBOUNCE_MS)) {
            processed.presence_debounced = 1;
            presence_last_trigger = now;
        } else if (!raw.presence) {
            processed.presence_debounced = 0;
        }

        /* ── Puissance électrique ── */
        processed.voltage_rms = raw.voltage_rms;
        if (raw.data_valid & (1 << 4)) {
            processed.current_rms = Kalman_Update(&kf_current, raw.current_rms);
        }
        /* cos(phi) ≈ 0.85 pour charge résidentielle typique */
        processed.power_watts = processed.voltage_rms * processed.current_rms * 0.85f;

        /* ── Température de surface ── */
        if (raw.data_valid & (1 << 6)) {
            processed.surface_temp_filtered = Kalman_Update(&kf_surface_temp, raw.surface_temp);
        }

        /* ── MQ-135 → CO2 ppm (approximation) ── */
        processed.co2_ppm = MQ135_ToPPM(raw.air_quality_raw);

        /* ── Indice de confort ── */
        processed.comfort_index = Calculate_ComfortIndex(
            processed.temperature_filtered,
            processed.humidity_filtered);

        /* ── Détection anomalies locales (règles simples) ── */
        processed.anomaly_flags = 0x00;
        if (processed.temperature_filtered > 35.0f || processed.temperature_filtered < 5.0f)
            processed.anomaly_flags |= (1 << 0); /* Temp hors plage */
        if (processed.humidity_filtered > 85.0f)
            processed.anomaly_flags |= (1 << 1); /* Humidité trop haute */
        if (processed.power_watts > 3000.0f)
            processed.anomaly_flags |= (1 << 2); /* Surcharge électrique */
        if (processed.co2_ppm > 1000.0f)
            processed.anomaly_flags |= (1 << 3); /* CO2 élevé */

        /* Déclenchement buzzer si urgence */
        if (processed.anomaly_flags & (1 << 2)) {
            ActuatorCmd_t cmd = { .actuator_id = 5, .command = 1, .duration_ms = 500 };
            xQueueSend(xActuatorCmdQueue, &cmd, 0);
        }

        /* Envoi vers UART task */
        xQueueSend(xSensorProcessedQueue, &processed, 0);
    }
}

/* ══════════════════════════════════════════════════════════
 *  TÂCHE 3 : Contrôle actuateurs (Priorité 3)
 * ══════════════════════════════════════════════════════════ */
void Task_ActuatorControl(void *pvParameters)
{
    (void)pvParameters;
    ActuatorCmd_t cmd;
    TickType_t xLastWakeTime = xTaskGetTickCount();

    /* GPIO relais (actif bas avec modules opto-isolés) */
    const uint16_t relay_pins[4] = {
        GPIO_PIN_0, GPIO_PIN_1, GPIO_PIN_2, GPIO_PIN_3
    };

    for (;;) {
        /* Traitement des commandes en attente */
        while (xQueueReceive(xActuatorCmdQueue, &cmd, 0) == pdPASS) {

            if (cmd.actuator_id < 4) {
                /* Commande relais 0-3 */
                GPIO_PinState state = (cmd.command == 1) ? GPIO_PIN_RESET : GPIO_PIN_SET;
                HAL_GPIO_WritePin(GPIOB, relay_pins[cmd.actuator_id], state);

                /* Si durée limitée, programmer timer soft */
                if (cmd.duration_ms > 0) {
                    vTaskDelay(pdMS_TO_TICKS(cmd.duration_ms));
                    HAL_GPIO_WritePin(GPIOB, relay_pins[cmd.actuator_id], GPIO_PIN_SET);
                }

            } else if (cmd.actuator_id == 4) {
                /* Commande servo (angle 0-180°) */
                /* PWM: 1ms=0°, 2ms=180° sur période 20ms (50Hz) */
                uint32_t pulse = 1000 + ((uint32_t)cmd.command * 1000 / 180);
                /* pulse en µs → ticks timer */
                __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_1, pulse);

            } else if (cmd.actuator_id == 5) {
                /* Buzzer */
                HAL_GPIO_WritePin(GPIOB, GPIO_PIN_5,
                    (cmd.command == 1) ? GPIO_PIN_SET : GPIO_PIN_RESET);
                if (cmd.duration_ms > 0) {
                    vTaskDelay(pdMS_TO_TICKS(cmd.duration_ms));
                    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_5, GPIO_PIN_RESET);
                }
            }
        }

        vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(ACTUATOR_TASK_PERIOD_MS));
    }
}

/* ══════════════════════════════════════════════════════════
 *  TÂCHE 4 : Communication UART vers ESP32 (Priorité 2)
 * ══════════════════════════════════════════════════════════ */
void Task_UART_Communication(void *pvParameters)
{
    (void)pvParameters;
    SensorProcessedData_t data;
    char tx_buffer[UART_TX_BUFFER_SIZE];
    TickType_t xLastWakeTime = xTaskGetTickCount();

    for (;;) {
        if (xQueueReceive(xSensorProcessedQueue, &data, pdMS_TO_TICKS(150)) == pdPASS) {
            /* Sérialisation JSON compact */
            int len = snprintf(tx_buffer, UART_TX_BUFFER_SIZE,
                "{\"ts\":%lu,\"t\":%.1f,\"h\":%.1f,\"l\":%.0f,"
                "\"p\":%d,\"v\":%.1f,\"i\":%.3f,\"w\":%.1f,"
                "\"co2\":%.0f,\"st\":%.1f,\"ci\":%.1f,\"af\":%d}\n",
                data.timestamp_ms,
                data.temperature_filtered,
                data.humidity_filtered,
                data.luminosity_lux,
                data.presence_debounced,
                data.voltage_rms,
                data.current_rms,
                data.power_watts,
                data.co2_ppm,
                data.surface_temp_filtered,
                data.comfort_index,
                data.anomaly_flags);

            if (len > 0 && len < UART_TX_BUFFER_SIZE) {
                if (xSemaphoreTake(xUART_Mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
                    HAL_UART_Transmit(&huart1, (uint8_t*)tx_buffer, len, HAL_MAX_DELAY);
                    xSemaphoreGive(xUART_Mutex);
                }
            }
        }
        vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(UART_TASK_PERIOD_MS));
    }
}

/* ══════════════════════════════════════════════════════════
 *  FONCTIONS UTILITAIRES
 * ══════════════════════════════════════════════════════════ */

/**
 * @brief Initialise un filtre de Kalman 1D
 * @param kf        Pointeur vers la structure filtre
 * @param init_val  Valeur initiale d'estimation
 * @param R         Variance de mesure (bruit capteur)
 * @param Q         Bruit de processus (dynamique système)
 */
void Kalman_Init(KalmanFilter_t *kf, float init_val, float R, float Q)
{
    kf->estimate      = init_val;
    kf->error_estimate = 1.0f;
    kf->error_measure  = R;
    kf->process_noise  = Q;
    kf->kalman_gain    = 0.0f;
}

/**
 * @brief Met à jour le filtre de Kalman avec une nouvelle mesure
 * @param kf          Pointeur vers la structure filtre
 * @param measurement Mesure brute
 * @return Estimation filtrée
 *
 * Algorithme :
 *   1. Prédiction : P_k = P_{k-1} + Q
 *   2. Gain :       K_k = P_k / (P_k + R)
 *   3. Mise à jour : x̂_k = x̂_{k-1} + K_k*(z_k - x̂_{k-1})
 *   4. Covariance : P_k = (1 - K_k) * P_k
 */
float Kalman_Update(KalmanFilter_t *kf, float measurement)
{
    /* Étape de prédiction */
    kf->error_estimate += kf->process_noise;

    /* Calcul du gain */
    kf->kalman_gain = kf->error_estimate / (kf->error_estimate + kf->error_measure);

    /* Mise à jour de l'estimation */
    kf->estimate += kf->kalman_gain * (measurement - kf->estimate);

    /* Mise à jour de la covariance */
    kf->error_estimate = (1.0f - kf->kalman_gain) * kf->error_estimate;

    return kf->estimate;
}

/**
 * @brief Calcule la moyenne mobile sur une fenêtre glissante
 */
float MovingAverage_Update(float *buffer, uint8_t *index, float new_val, uint8_t window)
{
    buffer[*index] = new_val;
    *index = (*index + 1) % window;

    float sum = 0.0f;
    for (uint8_t i = 0; i < window; i++) {
        sum += buffer[i];
    }
    return sum / (float)window;
}

/**
 * @brief Calcule l'indice de confort thermique (ASHRAE 55)
 * @param temp     Température en °C
 * @param humidity Humidité relative en %
 * @return Indice 0-100 (100 = confort parfait)
 */
float Calculate_ComfortIndex(float temp, float humidity)
{
    /* Zone de confort: temp 20-26°C, humidité 30-60% */
    float temp_score = 100.0f - fabsf(temp - 23.0f) * 10.0f;
    float humid_score = 100.0f - fabsf(humidity - 45.0f) * 1.5f;

    temp_score  = fmaxf(0.0f, fminf(100.0f, temp_score));
    humid_score = fmaxf(0.0f, fminf(100.0f, humid_score));

    return (temp_score * 0.6f + humid_score * 0.4f);
}

/**
 * @brief Convertit lecture ADC MQ-135 en ppm CO2
 * @param adc_raw Valeur ADC 12 bits
 * @return CO2 en ppm (approximation courbe MQ-135)
 */
float MQ135_ToPPM(uint16_t adc_raw)
{
    /* Rs/R0 calculé depuis la tension de sortie */
    float voltage = ((float)adc_raw / ADC_RESOLUTION) * VREF_MV / 1000.0f;
    float rs = (VREF_MV / 1000.0f - voltage) / voltage * 10.0f; /* RL = 10kΩ */
    float ratio = rs / 3.7f; /* R0 calibré en air propre */

    /* Courbe log: CO2 ppm = a * (Rs/R0)^b (DATASHEET MQ-135) */
    float ppm = 110.47f * powf(ratio, -2.862f);

    return fmaxf(400.0f, fminf(10000.0f, ppm)); /* Clamp 400-10000 ppm */
}
