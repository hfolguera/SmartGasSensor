// Prototip de SOBRETAULA. HC-SR04 no qualificat per a diposits de combustible.
// ESP8266 NodeMCU / Wemos D1 mini, core 3.1.2, MQTT 2.5.3.
// Deep sleep: connectar D0 (GPIO16) amb RST.
#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <MQTT.h>
extern "C" {
#include <user_interface.h>
}
#include <math.h>
#include "config.h"

constexpr uint8_t TRIG_PIN = 5;
constexpr uint8_t ECHO_PIN = 4;
constexpr uint8_t SENSOR_ENABLE_PIN = 14;
constexpr unsigned SAMPLE_COUNT = 9;
constexpr unsigned MIN_VALID_SAMPLES = 5;
constexpr char FW_VERSION[] = "0.2.0-esp8266";

struct Reading {
  float distance;
  float spread;
  unsigned valid;
  const char *status;
};

WiFiClient transport;
MQTTClient mqtt(768);
uint32_t cycleNumber = 0;
uint32_t sessionNonce = 0;
struct RtcState {
  uint32_t magic;
  uint32_t nonce;
  uint32_t cycle;
  uint32_t checksum;
};
constexpr uint32_t RTC_MAGIC = 0x47534F49;

uint32_t rtcChecksum(const RtcState &state) {
  return state.magic ^ state.nonce ^ state.cycle ^ 0xA5C37E91;
}

void saveRtc() {
  RtcState state = {RTC_MAGIC, sessionNonce, cycleNumber, 0};
  state.checksum = rtcChecksum(state);
  ESP.rtcUserMemoryWrite(32, reinterpret_cast<uint32_t *>(&state), sizeof(state));
}

void restoreRtc() {
  RtcState state = {};
  bool valid = ESP.rtcUserMemoryRead(32, reinterpret_cast<uint32_t *>(&state), sizeof(state));
  if (valid && ESP.getResetInfoPtr()->reason == REASON_DEEP_SLEEP_AWAKE &&
      state.magic == RTC_MAGIC && state.checksum == rtcChecksum(state)) {
    sessionNonce = state.nonce;
    cycleNumber = state.cycle;
  } else {
    sessionNonce = ESP.random();
    cycleNumber = 0;
  }
}

void sensorPower(bool enabled) {
  if (SWITCH_SENSOR_POWER) {
    digitalWrite(SENSOR_ENABLE_PIN, enabled ? HIGH : LOW);
  }
}

// No FreeRTOS a ESP8266: esperes de xarxa limitades i termini cooperatiu.
void sleepNow() {
  digitalWrite(TRIG_PIN, LOW);
  sensorPower(false);
  // El pulldown extern del NPN mante el sensor apagat quan el GPIO flota.
  saveRtc();
  WiFi.mode(WIFI_OFF);
  uint64_t periodUs = uint64_t(SAMPLE_PERIOD_SECONDS) * 1000000ULL;
  uint64_t elapsedUs = uint64_t(millis()) * 1000ULL;
  uint64_t sleepUs = periodUs > elapsedUs ? periodUs - elapsedUs : periodUs;
  if (sleepUs > ESP.deepSleepMax()) sleepUs = ESP.deepSleepMax();
  Serial.flush();
  ESP.deepSleep(sleepUs, WAKE_RF_DEFAULT);
  while (true) delay(1000);
}

void checkDeadline() {
  if (!BENCH_MODE && millis() >= MAX_AWAKE_MS) sleepNow();
}

Reading measure() {
  float values[SAMPLE_COUNT];
  unsigned count = 0;
  bool hadEcho = false;
  sensorPower(true);
  delay(150);
  for (unsigned i = 0; i < SAMPLE_COUNT; ++i) {
    digitalWrite(TRIG_PIN, LOW);
    delayMicroseconds(3);
    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(12);
    digitalWrite(TRIG_PIN, LOW);
    unsigned long pulse = pulseIn(ECHO_PIN, HIGH, 30000UL);
    if (pulse > 0) {
      hadEcho = true;
      // Velocitat nominal a ~20 C; sense compensacio de temperatura.
      float distance = pulse * 0.343f / 2.0f;
      if (distance >= MIN_DISTANCE_MM && distance <= MAX_DISTANCE_MM) {
        values[count++] = distance;
      }
    }
    delay(65);
  }
  digitalWrite(TRIG_PIN, LOW);
  sensorPower(false);
  if (count < MIN_VALID_SAMPLES) {
    return {NAN, NAN, count, count > 0 ? "insufficient_samples" :
                                   (hadEcho ? "out_of_range" : "no_echo")};
  }
  for (unsigned i = 1; i < count; ++i) {
    float value = values[i];
    unsigned j = i;
    while (j > 0 && values[j - 1] > value) {
      values[j] = values[j - 1];
      --j;
    }
    values[j] = value;
  }
  float spread = values[count - 1] - values[0];
  float median = count % 2 ? values[count / 2] :
                 (values[count / 2 - 1] + values[count / 2]) / 2.0f;
  if (spread > MAX_SPREAD_MM) return {NAN, spread, count, "unstable"};
  return {median, spread, count, "ok"};
}

void makePayload(const Reading &r, char *buffer, size_t capacity, bool wifi) {
  char distance[24], spread[24], rssi[16];
  if (isfinite(r.distance)) snprintf(distance, sizeof(distance), "%.1f", r.distance);
  else snprintf(distance, sizeof(distance), "null");
  if (isfinite(r.spread)) snprintf(spread, sizeof(spread), "%.1f", r.spread);
  else snprintf(spread, sizeof(spread), "null");
  if (wifi) snprintf(rssi, sizeof(rssi), "%d", int(WiFi.RSSI()));
  else snprintf(rssi, sizeof(rssi), "null");
  // Nonce per arrencada a fred + comptador RTC entre deep sleeps.
  // No mesurem bateria en aquesta revisio: null significa no disponible.
  snprintf(buffer, capacity,
    "{\"schema_version\":1,\"device_id\":\"%s\","
    "\"sample_id\":\"%s-%08lx-%lu\",\"distance_mm\":%s,"
    "\"spread_mm\":%s,\"valid_samples\":%u,\"measurement_status\":\"%s\","
    "\"battery_mv\":null,\"wifi_rssi_dbm\":%s,\"awake_ms\":%lu,"
    "\"sample_period_s\":%lu,\"firmware_version\":\"%s\"}",
    DEVICE_ID, DEVICE_ID, (unsigned long)sessionNonce, (unsigned long)cycleNumber,
    distance, spread, r.valid, r.status, rssi, millis(),
    SAMPLE_PERIOD_SECONDS, FW_VERSION);
}

void setup() {
  digitalWrite(SENSOR_ENABLE_PIN, LOW);
  pinMode(SENSOR_ENABLE_PIN, OUTPUT);
  pinMode(TRIG_PIN, OUTPUT);
  digitalWrite(TRIG_PIN, LOW);
  pinMode(ECHO_PIN, INPUT);
  Serial.begin(115200);
  delay(150);
  Serial.printf("\nGasoil %s | mode=%s\n", FW_VERSION, BENCH_MODE ? "banc" : "MQTT");

  restoreRtc();
  if (BENCH_MODE) {
    WiFi.mode(WIFI_OFF);
    return;
  }

  ++cycleNumber;
  Reading reading = measure();
  char payload[640];
  makePayload(reading, payload, sizeof(payload), false);
  Serial.println(payload);

  checkDeadline();
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS && millis() < MAX_AWAKE_MS) delay(50);
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi timeout; aquesta lectura no s'ha enviat.");
    Serial.flush();
    sleepNow();
  }

  // TCP/IP en LAN per al prototip. Sense TLS: no publicar aquest port a Internet.
  checkDeadline();
  // IP numerica per evitar afegir un timeout DNS al cicle de bateria.
  IPAddress brokerIp;
  if (!brokerIp.fromString(MQTT_HOST)) {
    Serial.println("MQTT_HOST ha de ser una IP numerica de la LAN.");
    sleepNow();
  }
  transport.setTimeout(MQTT_TIMEOUT_MS);
  if (!transport.connect(brokerIp, MQTT_PORT)) {
    Serial.println("TCP timeout/error; torno a dormir.");
    sleepNow();
  }
  checkDeadline();
  mqtt.begin(brokerIp, MQTT_PORT, transport);
  mqtt.setOptions(10, true, MQTT_TIMEOUT_MS);
  String clientId = String(DEVICE_ID) + "-" + WiFi.macAddress();
  bool connected = MQTT_USER[0] ? mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASSWORD, true)
                                : mqtt.connect(clientId.c_str(), true);
  checkDeadline();
  if (connected) {
    makePayload(reading, payload, sizeof(payload), true);
    // retain=false: el servidor conserva l'historic i l'ultim estat.
    // QoS 1: la biblioteca espera PUBACK abans de retornar exit.
    bool sent = mqtt.publish(MQTT_TOPIC, payload, false, 1);
    Serial.println(payload);
    Serial.println(sent ? "MQTT confirmat pel broker." : "MQTT sense confirmacio.");
    mqtt.disconnect();
  } else {
    Serial.printf("MQTT error=%d, retorn=%d\n", int(mqtt.lastError()), int(mqtt.returnCode()));
  }
  // No guardem cua local ni reintentem indefinidament en aquesta versio.
  Serial.println("Deep sleep fins al proper cicle.");
  Serial.flush();
  sleepNow();
}

void loop() {
  if (BENCH_MODE) {
    ++cycleNumber;
    Reading reading = measure();
    char payload[640];
    makePayload(reading, payload, sizeof(payload), false);
    Serial.println(payload);
    delay(5000);
  }
}