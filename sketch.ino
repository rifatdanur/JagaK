#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>

// ==========================================
// 1. KONFIGURASI PIN AMAN ESP32-S3 (VERSI RAPI)
// ==========================================
#define PIN_POT         10  // ADC1 (Kiri): Simulasi % Kematangan Buah Kakao (0-100%)
#define PIN_PIR         4   // RTC GPIO (Kiri): Sensor Gerak PIR
#define PIN_BTN_BLE     5   // Kiri: Tombol Hijau (Toggle BLE Whitelist Petani)
#define PIN_BTN_AUDIO   6   // Kiri: Tombol Biru (Simulasi Trigger Audio AI TinyML)
#define PIN_DHT         7   // Kiri: Sensor Suhu & Kelembapan Kebun
#define PIN_I2C_SDA     42  // Kanan: OLED SDA
#define PIN_I2C_SCL     41  // Kanan: OLED SCL
#define PIN_LED_RED     40  // Kanan: Lampu Sorot LED (Alarm Pencuri Level 2)
#define PIN_LED_YELLOW  39  // Kanan: Indikator Zona Merah (Kematangan Tinggi)
#define PIN_BUZZER      38  // Kanan: Buzzer (Sirine Pencuri / Ultrasonik Hewan)

#define DHTTYPE         DHT22
#define SCREEN_WIDTH    128
#define SCREEN_HEIGHT   64

// ==========================================
// 2. KONFIGURASI WI-FI WOKWI & MQTT (GRATIS)
// ==========================================
const char* WIFI_SSID     = "Wokwi-GUEST";
const char* WIFI_PASS     = "";
const char* MQTT_SERVER   = "broker.hivemq.com";
const int   MQTT_PORT     = 1883;

// Topik MQTT (sinkron dengan Dashboard Python app.py)
const char* TOPIC_TELEMETRY = "cacaoguard/lamtim/blok_b/telemetry";
const char* TOPIC_EVENT     = "cacaoguard/lamtim/blok_b/event";
const char* TOPIC_CMD       = "cacaoguard/lamtim/blok_b/cmd";

// ==========================================
// 3. OBJEK & VARIABEL STATUS SISTEM
// ==========================================
WiFiClient espClient;
PubSubClient mqtt(espClient);
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
DHT dht(PIN_DHT, DHTTYPE);

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
float tempC = 28.0;
float humPct = 75.0;
int anomalyCount = 0;
const int nightRiskFactor = 100;

unsigned long lastTelemetryMs = 0;
unsigned long lastMqttRetryMs = 0;
unsigned long lastOledMs = 0;
unsigned long lastSirenToggleMs = 0;
bool sirenHighTone = false;

// ==========================================
// 4. DYNAMIC RISK SCORING
// ==========================================
void updateRiskAndSensors() {
  int potRaw = analogRead(PIN_POT);
  ripenessPct = map(potRaw, 0, 4095, 0, 100);

  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (!isnan(t) && !isnan(h)) {
    tempC = t;
    humPct = h;
  }

  float anomalyScore = min(anomalyCount * 25, 100);
  riskScore = (0.50f * ripenessPct) + (0.30f * nightRiskFactor) + (0.20f * anomalyScore);

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
// 5. MQTT PUBLISH (JSON)
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
  doc["node_id"] = "BLOK_B_LAMTIM";
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
  doc["node_id"] = "BLOK_B_LAMTIM";
  doc["ripeness_pct"] = ripenessPct;
  doc["risk_score"] = round(riskScore);
  doc["zone"] = zoneStatus;
  doc["temp_c"] = round(tempC * 10) / 10.0;
  doc["hum_pct"] = round(humPct * 10) / 10.0;
  doc["ble_owner"] = bleOwnerPresent;
  doc["anomaly_count"] = anomalyCount;
  doc["status"] = modeToString(currentMode);

  char payload[256];
  serializeJson(doc, payload);

  if (mqtt.connected()) {
    mqtt.publish(TOPIC_TELEMETRY, payload);
  }
}

// ==========================================
// 6. ALGORITMA KEPUTUSAN KEAMANAN
// ==========================================
void handleSecurityTrigger(const char* source) {
  Serial.printf("\n>>> TRIGGER TERDETEKSI DARI: %s <<<\n", source);

  if (bleOwnerPresent) {
    Serial.println("[WHITELIST] Tag BLE Pemilik Kebun terdeteksi -> Alarm DIBATALKAN.");
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
  Serial.println("[ALERT LEVEL 2] Bukan Pemilik! Lampu Sorot + Sirine AKTIF, Kirim Wake-Cam ke AI...");
  publishEvent(source, "WAKE_CAM_THIEF_ALERT");
}

// ==========================================
// 7. CALLBACK PERINTAH DARI DASHBOARD AI
// ==========================================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String msg = "";
  for (unsigned int i = 0; i < length; i++) {
    msg += (char)payload[i];
  }
  msg.trim();
  msg.toUpperCase();

  Serial.print("[MQTT CMD DARI AI] ");
  Serial.println(msg);

  if (msg == "THIEF" || msg == "PERSON") {
    if (!bleOwnerPresent) {
      currentMode = MODE_THIEF_ALARM;
      alarmStartMs = millis();
      digitalWrite(PIN_LED_RED, HIGH);
      Serial.println("-> AI Konfirmasi MANUSIA: Sirine & Lampu Sorot ON!");
    }
  } else if (msg == "ANIMAL") {
    currentMode = MODE_ANIMAL_DETERRENT;
    alarmStartMs = millis();
    digitalWrite(PIN_LED_RED, LOW);
    Serial.println("-> AI Konfirmasi HEWAN: Mode Pengusir Ultrasonik ON!");
  } else if (msg == "CLEAR" || msg == "STOP") {
    currentMode = MODE_STANDBY;
    digitalWrite(PIN_LED_RED, LOW);
    noTone(PIN_BUZZER);
    Serial.println("-> Perintah CLEAR: Kembali ke Standby.");
  }
}

// ==========================================
// 8. AKTUATOR NON-BLOCKING
// ==========================================
void updateActuators() {
  unsigned long now = millis();

  if (currentMode != MODE_STANDBY && (now - alarmStartMs >= ALARM_DURATION_MS)) {
    currentMode = MODE_STANDBY;
    digitalWrite(PIN_LED_RED, LOW);
    noTone(PIN_BUZZER);
    Serial.println("[SYSTEM] Durasi aksi selesai -> Kembali ke Mode Standby.");
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

// ==========================================
// 9. TAMPILAN OLED SSD1306
// ==========================================
void updateOLED() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  display.setCursor(0, 0);
  display.println("CacaoGuard [BLOK B]");
  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);

  display.setCursor(0, 14);
  display.printf("Zona  : %s (%.0f)", zoneStatus.c_str(), riskScore);

  display.setCursor(0, 25);
  display.printf("Matang: %d%% | %.0fC", ripenessPct, tempC);

  display.setCursor(0, 36);
  display.printf("BLE Owner: %s", bleOwnerPresent ? "AKTIF (OK)" : "OFF");

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

// ==========================================
// 10. KONEKSI MQTT NON-BLOCKING
// ==========================================
void maintainMQTT() {
  if (WiFi.status() != WL_CONNECTED) return;
  if (mqtt.connected()) {
    mqtt.loop();
    return;
  }
  if (millis() - lastMqttRetryMs >= 5000) {
    lastMqttRetryMs = millis();
    String clientId = "CacaoGuard-S3-" + String(random(1000, 9999));
    Serial.print("Menghubungkan ke MQTT Broker... ");
    if (mqtt.connect(clientId.c_str())) {
      Serial.println("TERHUBUNG!");
      mqtt.subscribe(TOPIC_CMD);
    } else {
      Serial.print("Gagal, rc=");
      Serial.println(mqtt.state());
    }
  }
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);

  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_BTN_BLE, INPUT_PULLUP);
  pinMode(PIN_BTN_AUDIO, INPUT_PULLUP);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_LED_YELLOW, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);

  dht.begin();
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("OLED SSD1306 gagal inisialisasi!");
  } else {
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(10, 25);
    display.println("CacaoGuard AI Boot...");
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

  mqtt.setServer(MQTT_SERVER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
}

void loop() {
  unsigned long now = millis();
  maintainMQTT();

  bool bleReading = digitalRead(PIN_BTN_BLE);
  if (bleReading == LOW && lastBleBtnState == HIGH && (now - lastBleDebounce > 250)) {
    lastBleDebounce = now;
    bleOwnerPresent = !bleOwnerPresent;
    Serial.printf("[BLE WHITELIST] Mode Petani Pemilik: %s\n", bleOwnerPresent ? "AKTIF" : "MATI");
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

  if (now - lastOledMs >= 300) {
    lastOledMs = now;
    updateRiskAndSensors();
    updateOLED();
  }

  if (now - lastTelemetryMs >= 3000) {
    lastTelemetryMs = now;
    publishTelemetry();
  }
}
