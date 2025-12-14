#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>

/* ================= KONFIGURASI ANDA ================= */
const char* WIFI_SSID = "NAMA_WIFI_ANDA"; // <--- GANTI!
const char* WIFI_PASS = "PASSWORD_WIFI_ANDA"; // <--- GANTI!

const char* MQTT_BROKER = "172.27.27.133";
const int   MQTT_PORT   = 1883;
const char* TOPIC_CMD   = "goldfish/feeder/cmd";

/* ================= PIN SERVO ================= */
#define SERVO_PIN 12             // <--- PIN DATA SERVO
#define SERVO_OPEN_US  2000      // Nilai Mikrosekon untuk posisi BUKA
#define SERVO_CLOSE_US 1000     // Nilai Mikrosekon untuk posisi TUTUP

Servo feeder;

/* ================= MQTT CLIENT ================= */
WiFiClient espClient;
PubSubClient client(espClient);

/* ================= FEED STATE MACHINE ================= */
bool feeding = false;
int totalTurns = 0;
int currentTurn = 0;

unsigned long openMs = 700;
unsigned long gapMs  = 600;
unsigned long timer  = 0;

enum FeedState {
  IDLE,
  SERVO_OPEN,
  SERVO_HOLD,
  SERVO_GAP
};

FeedState state = IDLE;

/* ================= MQTT CALLBACK ================= */
void callback(char* topic, byte* payload, unsigned int length) {
  Serial.println("[MQTT] Command received");

  char buffer[256];
  if (length >= sizeof(buffer)) length = sizeof(buffer) - 1; 
  memcpy(buffer, payload, length);
  buffer[length] = '\0';
  
  StaticJsonDocument<256> doc;
  DeserializationError error = deserializeJson(doc, buffer);
  
  if (error) {
    Serial.print("[MQTT] JSON ERROR: ");
    Serial.println(error.f_str());
    return;
  }

  // Debugging: Cetak isi JSON yang diterima
  Serial.print("[MQTT] Payload: ");
  serializeJson(doc, Serial);
  Serial.println();

  if (strcmp(doc["action"], "feed") != 0) {
    Serial.println("[MQTT] Action not 'feed', ignored.");
    return;
  }

  if (feeding) {
    Serial.println("[FEED] Still feeding, command ignored.");
    return;
  }

  totalTurns = doc["turns"] | 1;
  openMs     = doc["duration"] | 700;
  gapMs      = doc["gap"] | 600;

  // Set state machine untuk memulai
  currentTurn = 0;
  feeding     = true;
  state       = SERVO_OPEN;

  Serial.printf("[FEED] START | turns=%d, openMs=%lu, gapMs=%lu\n", totalTurns, openMs, gapMs);
}

/* ================= SETUP ================= */
void setup() {
  Serial.begin(115200);

  // --- WIFI ---
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[WIFI] Connecting");
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(300);
    Serial.print(".");
    attempts++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[WIFI] Connected");
    Serial.print("IP Address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n[WIFI] Failed to connect. Check SSID/PASS.");
  }


  // --- MQTT ---
  client.setServer(MQTT_BROKER, MQTT_PORT);
  client.setCallback(callback);

  // --- SERVO ---
  feeder.setPeriodHertz(50);
  // Rentang 500-2500 adalah rentang standar untuk mikrosekon
  feeder.attach(SERVO_PIN, 500, 2500); 
  feeder.writeMicroseconds(SERVO_CLOSE_US); // Posisi awal tertutup
}

void reconnect() {
  while (!client.connected()) {
    Serial.print("[MQTT] Attempting connection...");
    if (client.connect("ESP32_FEEDER")) {
      client.subscribe(TOPIC_CMD);
      Serial.println("connected & subscribed");
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" retrying in 5 seconds");
      delay(5000);
    }
  }
}

/* ================= LOOP ================= */
void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  // Logika Feeding Servo
  if (!feeding) return;

  unsigned long now = millis();

  switch (state) {
    case SERVO_OPEN:
      Serial.printf("[FEED] Turn %d/%d: Set Servo OPEN (%d us)\n", currentTurn + 1, totalTurns, SERVO_OPEN_US);
      feeder.writeMicroseconds(SERVO_OPEN_US);
      timer = now;
      state = SERVO_HOLD;
      break;

    case SERVO_HOLD:
      if (now - timer >= openMs) {
        Serial.printf("[FEED] Turn %d/%d: Set Servo CLOSE (%d us)\n", currentTurn + 1, totalTurns, SERVO_CLOSE_US);
        feeder.writeMicroseconds(SERVO_CLOSE_US);
        timer = now;
        state = SERVO_GAP;
      }
      break;

    case SERVO_GAP:
      if (now - timer >= gapMs) {
        currentTurn++;
        if (currentTurn >= totalTurns) {
          feeding = false;
          state   = IDLE;
          Serial.println("[FEED] DONE. System IDLE.");
        } else {
          Serial.println("[FEED] Gap done, starting next turn.");
          state = SERVO_OPEN;
        }
      }
      break;

    case IDLE:
    default:
      break;
  }
}