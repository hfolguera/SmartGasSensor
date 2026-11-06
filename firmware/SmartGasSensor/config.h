#pragma once

#include "secrets.h"

// Configuracio compartida. Les credencials nomes van al secrets.h local.
// IP de la LAN accessible des del Wi-Fi, no una IP ClusterIP interna de k8s.
constexpr char MQTT_HOST[] = "192.168.1.216";
constexpr unsigned short MQTT_PORT = 1883;
constexpr char DEVICE_ID[] = "NodeMCU_ESP32"; // Lletres, nombres i guions.
constexpr char MQTT_TOPIC[] = "home/gas/telemetry";

// true: lectures per serial cada 5 s, sense Wi-Fi ni deep sleep.
// false: una lectura + publicacio MQTT + deep sleep.
constexpr bool BENCH_MODE = false;
// false: sensor alimentat continuament; no es commuta amb el firmware.
// true: requereix circuit extern de commutacio; control D5/GPIO14.
constexpr bool SWITCH_SENSOR_POWER = false;
constexpr unsigned long SAMPLE_PERIOD_SECONDS = 3600;
constexpr unsigned long MAX_AWAKE_MS = 30000;
constexpr unsigned long WIFI_TIMEOUT_MS = 12000;
constexpr unsigned long MQTT_TIMEOUT_MS = 2500;
constexpr float MIN_DISTANCE_MM = 20.0f;
constexpr float MAX_DISTANCE_MM = 4000.0f;
// Llindar inicial experimental: dispersio max-min de la tanda.
constexpr float MAX_SPREAD_MM = 30.0f;

