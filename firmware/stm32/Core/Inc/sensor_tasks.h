/**
 * @file    sensor_tasks.h
 * @brief   Déclarations des tâches FreeRTOS pour acquisition capteurs
 * @author  Smart Room System
 * @version 1.0.0
 *
 * Architecture FreeRTOS :
 *   Task1_SensorAcq   : Priorité HAUTE  - Lecture ADC/GPIO (100ms)
 *   Task2_Processing  : Priorité MEDIUM - Filtrage Kalman + moyenne mobile
 *   Task3_Actuator    : Priorité MEDIUM - Contrôle PID relais/servo
 *   Task4_UART_Comm   : Priorité LOW    - Envoi données vers ESP32
 */

#ifndef SENSOR_TASKS_H
#define SENSOR_TASKS_H

#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "semphr.h"
#include "timers.h"
#include "stm32f4xx_hal.h"

/* ─── Constantes système ─── */
#define SENSOR_TASK_PERIOD_MS       100U
#define PROCESSING_TASK_PERIOD_MS   100U
#define ACTUATOR_TASK_PERIOD_MS     50U
#define UART_TASK_PERIOD_MS         200U

#define MOVING_AVG_WINDOW           5U
#define ADC_RESOLUTION              4096U   /* 12 bits */
#define VREF_MV                     3300U   /* 3.3V */
#define ADC_CHANNELS_COUNT          4U

#define UART_TX_BUFFER_SIZE         256U
#define SENSOR_QUEUE_SIZE           10U

/* ─── Structures de données ─── */

/**
 * @brief Lecture brute d'un capteur
 */
typedef struct {
    uint32_t timestamp_ms;      /**< Timestamp HAL_GetTick() */
    float    temperature;       /**< DHT22 °C */
    float    humidity;          /**< DHT22 % */
    uint16_t luminosity_raw;    /**< BH1750 lux brut */
    uint8_t  presence;          /**< PIR HC-SR501 0/1 */
    float    voltage_rms;       /**< ZMPT101B V */
    float    current_rms;       /**< ACS712 A */
    float    surface_temp;      /**< DS18B20 °C */
    uint16_t air_quality_raw;   /**< MQ-135 ADC raw */
    uint8_t  data_valid;        /**< Bitmask validité capteurs */
} SensorRawData_t;

/**
 * @brief Données traitées après filtrage
 */
typedef struct {
    uint32_t timestamp_ms;
    float    temperature_filtered;    /**< Kalman filtered */
    float    humidity_filtered;
    float    luminosity_lux;          /**< Converti en lux */
    uint8_t  presence_debounced;      /**< Anti-rebond 500ms */
    float    voltage_rms;
    float    current_rms;
    float    power_watts;             /**< V * I * cos(phi) */
    float    surface_temp_filtered;
    float    co2_ppm;                 /**< Converti depuis MQ-135 */
    float    comfort_index;           /**< Calculé: f(temp, humidity) */
    uint8_t  anomaly_flags;           /**< Bitmask anomalies locales */
} SensorProcessedData_t;

/**
 * @brief État filtre de Kalman 1D
 */
typedef struct {
    float estimate;         /**< Estimation courante x̂ */
    float error_estimate;   /**< Variance d'estimation P */
    float error_measure;    /**< Variance de mesure R */
    float kalman_gain;      /**< Gain K */
    float process_noise;    /**< Bruit de processus Q */
} KalmanFilter_t;

/**
 * @brief Commande actuateur
 */
typedef struct {
    uint8_t  actuator_id;   /**< 0=relay1..3=relay4, 4=servo, 5=buzzer */
    uint8_t  command;       /**< 0=OFF, 1=ON, 2..100=PWM% */
    uint16_t duration_ms;   /**< 0 = permanent */
} ActuatorCmd_t;

/* ─── Handles globaux FreeRTOS ─── */
extern QueueHandle_t  xSensorRawQueue;        /**< Raw → Processing */
extern QueueHandle_t  xSensorProcessedQueue;  /**< Processing → UART */
extern QueueHandle_t  xActuatorCmdQueue;      /**< Commands → Actuator */
extern SemaphoreHandle_t xI2C_Mutex;          /**< Accès exclusif I2C */
extern SemaphoreHandle_t xUART_Mutex;         /**< Accès exclusif UART */

/* ─── Prototypes tâches ─── */
void Task_SensorAcquisition(void *pvParameters);
void Task_DataProcessing(void *pvParameters);
void Task_ActuatorControl(void *pvParameters);
void Task_UART_Communication(void *pvParameters);

/* ─── Fonctions utilitaires ─── */
float Kalman_Update(KalmanFilter_t *kf, float measurement);
void  Kalman_Init(KalmanFilter_t *kf, float init_val, float R, float Q);
float MovingAverage_Update(float *buffer, uint8_t *index, float new_val, uint8_t window);
float Calculate_ComfortIndex(float temp, float humidity);
float MQ135_ToPPM(uint16_t adc_raw);
float ZMPT101B_ToVrms(uint16_t *adc_samples, uint16_t count);
float ACS712_ToCurrent(uint16_t adc_raw, float sensitivity_mv_a);

#endif /* SENSOR_TASKS_H */
