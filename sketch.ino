#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <time.h>

// ==========================================
// 1. KONFIGURASI PIN AMAN ESP32-S3
// ==========================================
#define PIN_POT         10  // ADC1 (Kiri): Simulasi % Kematangan Buah (0-100%)
#define PIN_PIR         4   // RTC GPIO (Kiri): Sensor Gerak PIR
#define PIN_BTN_BLE     5   // Kiri: Tombol Hijau (Toggle BLE Whitelist Petani)
#define PIN_BTN_AUDIO   6   // Kiri: Tombol Biru (Simulasi Audio AI TinyML)
#define PIN_DHT         7   // Kiri: Sensor DHT22 (Driver Native ESP32-S3)
#define PIN_I2C_SDA     42  // Kanan: OLED SDA
#define PIN_I2C_SCL     41  // Kanan: OLED SCL
#define PIN_LED_RED     40  // Kanan: Lampu Sorot LED (Alarm Pencuri)
#define PIN_LED_YELLOW  39  // Kanan: Indikator Zona Merah
#define PIN_BUZZER      38  // Kanan: Buzzer (Sirine / Ultrasonik)

#define SCREEN_WIDTH    128
#define SCREEN_HEIGHT   64

// ==========================================
// 2. KONFIGURASI WI-FI WOKWI, NTP (WIB) & MQTT
// ==========================================
const char* WIFI_SSID     = "Wokwi-GUEST";
const char* WIFI_PASS     = "";
const char* MQTT_SERVER   = "broker.hivemq.com";
const int   MQTT_PORT     = 1883;

// Sinkronisasi Waktu WIB (GMT+7 = 7 * 3600 detik) agar sama dengan waktu laptop
const long  GMT_OFFSET_SEC      = 7 * 3600;
const int   DAYLIGHT_OFFSET_SEC = 0;

const char* TOPIC_TELEMETRY = "jagak/lamtim/telemetry";
const char* TOPIC_EVENT     = "jagak/lamtim/event";
const char* TOPIC_CMD       = "jagak/lamtim/cmd";

// ==========================================
// 3. OBJEK & VARIABEL STATUS (TANPA NILAI DUMMY DHT)
// ==========================================
WiFiClient espClient;
PubSubClient mqtt(espClient);
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
portMUX_TYPE dhtMux = portMUX_INITIALIZER_UNLOCKED;

enum AlarmMode {
  MODE_STANDBY,
  MODE_OWNER_WHITELIST,
  MODE_ANIMAL_DETERRENT,
  MODE_THIEF_ALARM
};

AlarmMode currentMode = MODE_STANDBY;
unsigned long alarmStartMs = 0;
const unsigned long ALARM_DURATION_MS = 6000;

bool bleOwnerPresent = false;
bool lastPirState = LOW;
bool lastBleBtnState = HIGH;
bool lastAudioBtnState = HIGH;
unsigned long lastBleDebounce = 0;
unsigned long lastAudioDebounce = 0;

int ripenessPct = 0;
float riskScore = 0.0;
String zoneStatus = "HIJAU";

// DHT22 mulai sebagai NULL (NAN) sampai benar-benar terbaca dari sensor
float tempC = NAN;
float humPct = NAN;
bool dhtValid = false;

int anomalyCount = 0;
unsigned long lastTelemetryMs = 0;
unsigned long lastMqttRetryMs = 0;
unsigned long lastOledMs = 0;
unsigned long lastDhtMs = 0;
unsigned long lastSirenToggleMs = 0;
bool sirenHighTone = false;

// ==========================================
// 4. AMBIL WAKTU WIB REAL-TIME (SINKRON NTP)
// ==========================================
String getTimeWIB() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo, 10)) {
    return "null";
  }
  char buf[16];
  strftime(buf, sizeof(buf), "%H:%M:%S", &timeinfo);
  return String(buf);
}

int getNightRiskFactor() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo, 10)) {
    return 100;
  }
  int jam = timeinfo.tm_hour;
  return (jam >= 18 || jam <= 5) ? 100 : 20;
}

// ==========================================
// 5. DRIVER NATIVE DHT22 KHUSUS ESP32-S3 (TANPA LIBRARY LUAR!)
//    Tahan gangguan Wi-Fi & 100% akurat di Wokwi maupun Hardware Asli
// ==========================================
int tungguSinyalDHT(int pin, int level, int timeoutUs) {
  int64_t mulai = esp_timer_get_time();
  while (digitalRead(pin) == level) {
    if ((esp_timer_get_time() - mulai) > timeoutUs) return -1;
  }
  return (int)(esp_timer_get_time() - mulai);
}

bool bacaSensorDHT22Native(int pin, float &outTemp, float &outHum, const char* &errReason) {
  uint8_t data[5] = {0, 0, 0, 0, 0};

  // 1. Kirim sinyal START (LOW selama 2ms) ke DHT22
  pinMode(pin, OUTPUT);
  digitalWrite(pin, LOW);
  delay(2);

  // 2. Masuk critical section agar Wi-Fi tidak mengganggu timing mikrodetik
  portENTER_CRITICAL(&dhtMux);
  pinMode(pin, INPUT_PULLUP);

  // Tunggu respon DHT22: lepas HIGH -> 80us LOW -> 80us HIGH
  if (tungguSinyalDHT(pin, HIGH, 200) < 0) { portEXIT_CRITICAL(&dhtMux); errReason = "NO_RESPONSE_1"; return false; }
  if (tungguSinyalDHT(pin, LOW,  200) < 0) { portEXIT_CRITICAL(&dhtMux); errReason = "NO_RESPONSE_2"; return false; }
  if (tungguSinyalDHT(pin, HIGH, 200) < 0) { portEXIT_CRITICAL(&dhtMux); errReason = "NO_RESPONSE_3"; return false; }

  // 3. Baca 40 bit (5 byte): HIGH > 45us = bit 1, lainnya = bit 0
  for (int i = 0; i < 40; i++) {
    if (tungguSinyalDHT(pin, LOW, 150) < 0) { portEXIT_CRITICAL(&dhtMux); errReason = "BIT_LOW_TIMEOUT"; return false; }
    int durasiHigh = tungguSinyalDHT(pin, HIGH, 150);
    if (durasiHigh < 0) { portEXIT_CRITICAL(&dhtMux); errReason = "BIT_HIGH_TIMEOUT"; return false; }

    data[i / 8] <<= 1;
    if (durasiHigh > 45) {
      data[i / 8] |= 1;
    }
  }
  portEXIT_CRITICAL(&dhtMux);

  // 4. Verifikasi Checksum (Byte 4 == jumlah Byte 0..3)
  uint8_t checksum = (data[0] + data[1] + data[2] + data[3]) & 0xFF;
  if (data[4] != checksum) {
    errReason = "CHECKSUM_ERROR";
    return false;
  }

  // 5. Konversi 4 byte menjadi Kelembapan (%) & Suhu (°C)
  outHum = ((data[0] << 8) | data[1]) * 0.1f;
  int16_t rawTemp = ((data[2] & 0x7F) << 8) | data[3];
  outTemp = rawTemp * 0.1f;
  if (data[2] & 0x80) outTemp = -outTemp;

  errReason = "OK";
  return true;
}

void readDHT22() {
  float t = NAN, h = NAN;
  const char* reason = "";

  if (bacaSensorDHT22Native(PIN_DHT, t, h, reason)) {
    bool berubah = !dhtValid || abs(t - tempC) >= 0.1f || abs(h - humPct) >= 0.1f;
    tempC = t;
    humPct = h;
    dhtValid = true;
    if (berubah) {
      Serial.printf("[%s] [DHT22] Suhu: %.1f C | Kelembapan: %.1f%%\n",
                    getTimeWIB().c_str(), tempC, humPct);
    }
  } else {
    // Jika sensor lepas / tidak merespon -> nilai jadi null
    dhtValid = false;
    tempC = NAN;
    humPct = NAN;
    Serial.printf("[%s] [DHT22] Tidak terbaca -> null (%s)\n",
                  getTimeWIB().c_str(), reason);
  }
}

// ==========================================
// 6. DYNAMIC RISK SCORING
// ==========================================
void updateRiskAndSensors() {
  int potRaw = analogRead(PIN_POT);
  ripenessPct = map(potRaw, 0, 4095, 0, 100);

  int nightRisk = getNightRiskFactor();
  float anomalyScore = min(anomalyCount * 25, 100);
  riskScore = (0.50f * ripenessPct) + (0.30f * nightRisk) + (0.20f * anomalyScore);

  if (riskScore >= 70.0f || ripenessPct >= 60) {
    zoneStatus = "MERAH";
    digitalWrite(PIN_LED_YELLOW, HIGH);
  } else if (riskScore >= 40.0f) {
    zoneStatus = "KUNING";
    digitalWrite(PIN_LED_YELLOW, (millis() / 1000) % 2);
  } else {
    zoneStatus = "HIJAU";
    digitalWrite(PIN_LED_YELLOW, LOW);
  }
}

// ==========================================
// 7. MQTT PUBLISH (JSON — NULL JIKA DHT TIDAK TERBACA)
// ==========================================
String modeToString(AlarmMode m) {
  switch (m) {
    case MODE_OWNER_WHITELIST:  return "OWNER_WHITELIST";
    case MODE_ANIMAL_DETERRENT: return "ANIMAL_ULTRASONIC";
    case MODE_THIEF_ALARM:      return "THIEF_ALARM";
    default:                    return "STANDBY";
  }
}

void publishEvent(const char* triggerSource, const char* actionResult) {
  JsonDocument doc;
  doc["node_id"] = "JAGAK_LAMTIM";
  doc["time_wib"] = getTimeWIB();
  doc["trigger"] = triggerSource;
  doc["ble_owner"] = bleOwnerPresent;
  doc["ripeness_pct"] = ripenessPct;
  doc["risk_score"] = round(riskScore);
  doc["zone"] = zoneStatus;
  doc["action"] = actionResult;

  char payload[256];
  serializeJson(doc, payload);
  Serial.print("[EVENT] ");
  Serial.println(payload);

  if (mqtt.connected()) {
    mqtt.publish(TOPIC_EVENT, payload);
  }
}

void publishTelemetry() {
  JsonDocument doc;
  doc["node_id"] = "JAGAK_LAMTIM";
  doc["time_wib"] = getTimeWIB();
  doc["ripeness_pct"] = ripenessPct;
  doc["risk_score"] = round(riskScore);
  doc["zone"] = zoneStatus;

  if (dhtValid) {
    doc["temp_c"] = round(tempC * 10) / 10.0;
    doc["hum_pct"] = round(humPct * 10) / 10.0;
  } else {
    doc["temp_c"] = nullptr;
    doc["hum_pct"] = nullptr;
  }

  doc["ble_owner"] = bleOwnerPresent;
  doc["anomaly_count"] = anomalyCount;
  doc["status"] = modeToString(currentMode);

  char payload[300];
  serializeJson(doc, payload);

  if (mqtt.connected()) {
    mqtt.publish(TOPIC_TELEMETRY, payload);
  }
}

// ==========================================
// 8. ALGORITMA KEPUTUSAN KEAMANAN
// ==========================================
void handleSecurityTrigger(const char* source) {
  Serial.printf("\n[%s] >>> TRIGGER DARI: %s <<<\n", getTimeWIB().c_str(), source);

  if (bleOwnerPresent) {
    Serial.println("[WHITELIST] BLE Petani terdeteksi -> Alarm DIBATALKAN.");
    currentMode = MODE_OWNER_WHITELIST;
    alarmStartMs = millis();
    digitalWrite(PIN_LED_RED, LOW);
    noTone(PIN_BUZZER);
    publishEvent(source, "OWNER_VERIFIED_SUPPRESSED");
    return;
  }

  anomalyCount++;
  currentMode = MODE_THIEF_ALARM;
  alarmStartMs = millis();
  digitalWrite(PIN_LED_RED, HIGH);
  Serial.println("[ALERT LEVEL 2] Bukan Pemilik! Lampu Sorot + Sirine AKTIF.");
  publishEvent(source, "WAKE_CAM_THIEF_ALERT");
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String msg = "";
  for (unsigned int i = 0; i < length; i++) {
    msg += (char)payload[i];
  }
  msg.trim();
  msg.toUpperCase();

  Serial.printf("[%s] [CMD DARI AI] %s\n", getTimeWIB().c_str(), msg.c_str());

  if (msg == "THIEF" || msg == "PERSON") {
    if (!bleOwnerPresent) {
      currentMode = MODE_THIEF_ALARM;
      alarmStartMs = millis();
      digitalWrite(PIN_LED_RED, HIGH);
    }
  } else if (msg == "ANIMAL") {
    currentMode = MODE_ANIMAL_DETERRENT;
    alarmStartMs = millis();
    digitalWrite(PIN_LED_RED, LOW);
  } else if (msg == "CLEAR" || msg == "STOP") {
    currentMode = MODE_STANDBY;
    digitalWrite(PIN_LED_RED, LOW);
    noTone(PIN_BUZZER);
  }
}

// ==========================================
// 9. AKTUATOR & OLED SSD1306
// ==========================================
void updateActuators() {
  unsigned long now = millis();

  if (currentMode != MODE_STANDBY && (now - alarmStartMs >= ALARM_DURATION_MS)) {
    currentMode = MODE_STANDBY;
    digitalWrite(PIN_LED_RED, LOW);
    noTone(PIN_BUZZER);
    return;
  }

  if (currentMode == MODE_THIEF_ALARM) {
    if (now - lastSirenToggleMs >= 250) {
      lastSirenToggleMs = now;
      sirenHighTone = !sirenHighTone;
      tone(PIN_BUZZER, sirenHighTone ? 1400 : 800);
    }
  } else if (currentMode == MODE_ANIMAL_DETERRENT) {
    if (now - lastSirenToggleMs >= 400) {
      lastSirenToggleMs = now;
      sirenHighTone = !sirenHighTone;
      if (sirenHighTone) tone(PIN_BUZZER, 3200);
      else noTone(PIN_BUZZER);
    }
  } else {
    noTone(PIN_BUZZER);
  }
}

void updateOLED() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  display.setCursor(0, 0);
  display.printf("Jagak       %s", getTimeWIB().c_str());
  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);

  display.setCursor(0, 14);
  display.printf("Zona:%-6s Mat:%d%%", zoneStatus.c_str(), ripenessPct);

  display.setCursor(0, 25);
  if (dhtValid) {
    display.printf("T:%.1fC   H:%.1f%%", tempC, humPct);
  } else {
    display.println("T:null    H:null");
  }

  display.setCursor(0, 36);
  display.printf("BLE Owner: %s", bleOwnerPresent ? "AKTIF" : "OFF");

  display.setCursor(0, 48);
  if (currentMode == MODE_THIEF_ALARM) {
    display.println("!! ALARM PENCURI !!");
  } else if (currentMode == MODE_ANIMAL_DETERRENT) {
    display.println(">> USIR HEWAN <<");
  } else if (currentMode == MODE_OWNER_WHITELIST) {
    display.println("PETANI TERVERIFIKASI");
  } else {
    display.printf("Status: STANDBY %s", mqtt.connected() ? "[NET]" : "[OFF]");
  }

  display.display();
}

void maintainMQTT() {
  if (WiFi.status() != WL_CONNECTED) return;
  if (mqtt.connected()) {
    mqtt.loop();
    return;
  }
  if (millis() - lastMqttRetryMs >= 5000) {
    lastMqttRetryMs = millis();
    String clientId = "Jagak-S3-" + String(random(1000, 9999));
    Serial.print("Menghubungkan ke MQTT Broker... ");
    if (mqtt.connect(clientId.c_str())) {
      Serial.println("TERHUBUNG!");
      mqtt.subscribe(TOPIC_CMD);
      publishTelemetry();
    } else {
      Serial.printf("Gagal, rc=%d\n", mqtt.state());
    }
  }
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);

  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_BTN_BLE, INPUT_PULLUP);
  pinMode(PIN_BTN_AUDIO, INPUT_PULLUP);
  pinMode(PIN_DHT, INPUT_PULLUP);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_LED_YELLOW, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(10, 25);
    display.println("Jagak AIoT Boot...");
    display.display();
  }

  Serial.print("Menghubungkan Wi-Fi Wokwi");
  WiFi.begin(WIFI_SSID, WIFI_PASS, 6);
  unsigned long wifiStart = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - wifiStart < 15000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " TERHUBUNG!" : " Mode Offline.");

  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET_SEC, "pool.ntp.org", "time.nist.gov");

  mqtt.setServer(MQTT_SERVER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);

  readDHT22();
}

void loop() {
  unsigned long now = millis();
  maintainMQTT();

  bool bleReading = digitalRead(PIN_BTN_BLE);
  if (bleReading == LOW && lastBleBtnState == HIGH && (now - lastBleDebounce > 250)) {
    lastBleDebounce = now;
    bleOwnerPresent = !bleOwnerPresent;
    Serial.printf("[%s] [BLE WHITELIST] %s\n", getTimeWIB().c_str(), bleOwnerPresent ? "AKTIF" : "MATI");
    publishTelemetry();
  }
  lastBleBtnState = bleReading;

  bool audioReading = digitalRead(PIN_BTN_AUDIO);
  if (audioReading == LOW && lastAudioBtnState == HIGH && (now - lastAudioDebounce > 300)) {
    lastAudioDebounce = now;
    handleSecurityTrigger("AUDIO_TINYML_BRANCH_SNAP");
  }
  lastAudioBtnState = audioReading;

  bool pirReading = digitalRead(PIN_PIR);
  if (pirReading == HIGH && lastPirState == LOW) {
    handleSecurityTrigger("PIR_MOTION_SENSOR");
  }
  lastPirState = pirReading;

  updateActuators();

  // Baca DHT22 setiap 2 detik
  if (now - lastDhtMs >= 2000) {
    lastDhtMs = now;
    readDHT22();
  }

  if (now - lastOledMs >= 300) {
    lastOledMs = now;
    updateRiskAndSensors();
    updateOLED();
  }

  // Kirim telemetri setiap 2 detik agar Dashboard real-time makin responsif
  if (now - lastTelemetryMs >= 2000) {
    lastTelemetryMs = now;
    publishTelemetry();
  }
}
