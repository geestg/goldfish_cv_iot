#define MQTT_MAX_PACKET_SIZE 512

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>

/* ================= WIFI ================= */
const char* WIFI_SSID = "Efeeder";
const char* WIFI_PASS = "11111111";

/* ================= MQTT ================= */
const char* MQTT_BROKER = "172.27.27.133";
const int   MQTT_PORT   = 1883;
const char* TOPIC_CMD  = "goldfish/feeder/cmd";

/* ================= SERVO ================= */
#define SERVO_PIN        19
#define SERVO_OPEN_US   2000
#define SERVO_CLOSE_US  1000

Servo feeder;
bool servoAttached = false;

/* ================= MQTT ================= */
WiFiClient espClient;
PubSubClient client(espClient);

/* ================= FEED FSM ================= */
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

/* ================= COMMAND DEDUP ================= */
String lastExecutedRunId = "";

/* ================= SERVO HELPERS ================= */
void attachServoIfNeeded() {
  if (!servoAttached) {
    feeder.setPeriodHertz(50);
    feeder.attach(SERVO_PIN, 500, 2500);
    feeder.writeMicroseconds(SERVO_CLOSE_US);
    servoAttached = true;
    delay(50);  // stabilisasi singkat
  }
}

void detachServoIfNeeded() {
  if (servoAttached) {
    feeder.detach();
    servoAttached = false;
  }
}

/* ================= WIFI ================= */
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  Serial.print("[WIFI] Connecting");
  int retry = 0;
  while (WiFi.status() != WL_CONNECTED && retry < 30) {
    delay(300);
    Serial.print(".");
    retry++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[WIFI] Connected");
    Serial.print("[WIFI] IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n[WIFI] FAILED → RESTART");
    delay(2000);
    ESP.restart();
  }
}

/* ================= MQTT ================= */
void reconnectMQTT() {
  while (!client.connected()) {
    String clientId = "ESP32_FEEDER_" + WiFi.macAddress();
    Serial.print("[MQTT] Connecting as ");
    Serial.println(clientId);

    if (client.connect(clientId.c_str())) {
      client.subscribe(TOPIC_CMD);
      Serial.println("[MQTT] Connected & subscribed");
    } else {
      Serial.print("[MQTT] Failed, rc=");
      Serial.println(client.state());
      delay(3000);
    }
  }
}

/* ================= MQTT CALLBACK ================= */
void callback(char* topic, byte* payload, unsigned int length) {

  char buffer[256];
  if (length >= sizeof(buffer)) length = sizeof(buffer) - 1;
  memcpy(buffer, payload, length);
  buffer[length] = '\0';

  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, buffer)) return;

  if (!doc.containsKey("action") || strcmp(doc["action"], "feed") != 0) return;
  if (!doc.containsKey("run_id")) return;

  String runId = doc["run_id"].as<String>();

  if (runId == lastExecutedRunId) return;
  if (feeding) return;

  lastExecutedRunId = runId;

  totalTurns = doc["turns"] | 1;
  openMs     = doc["duration"] | 700;
  gapMs      = doc["gap"] | 600;

  if (totalTurns < 1) totalTurns = 1;
  if (totalTurns > 8) totalTurns = 8;  // batas aman

  currentTurn = 0;
  feeding     = true;
  state       = SERVO_OPEN;

  Serial.printf("[FEED] ACCEPTED | run_id=%s | turns=%d\n",
                runId.c_str(), totalTurns);
}

/* ================= SETUP ================= */
void setup() {
  Serial.begin(115200);
  delay(800);

  connectWiFi();

  client.setServer(MQTT_BROKER, MQTT_PORT);
  client.setCallback(callback);
  client.setBufferSize(512);

  detachServoIfNeeded();  // PASTIKAN SERVO MATI
  Serial.println("[SYSTEM] READY");
}

/* ================= LOOP ================= */
void loop() {

  if (!client.connected()) {
    reconnectMQTT();
  }
  client.loop();

  if (!feeding) return;

  unsigned long now = millis();

  switch (state) {

    case SERVO_OPEN:
      attachServoIfNeeded();
      Serial.printf("[FEED] Turn %d/%d → OPEN\n",
                    currentTurn + 1, totalTurns);
      feeder.writeMicroseconds(SERVO_OPEN_US);
      timer = now;
      state = SERVO_HOLD;
      break;

    case SERVO_HOLD:
      if (now - timer >= openMs) {
        Serial.printf("[FEED] Turn %d/%d → CLOSE\n",
                      currentTurn + 1, totalTurns);
        feeder.writeMicroseconds(SERVO_CLOSE_US);
        timer = now;
        state = SERVO_GAP;
      }
      break;

    case SERVO_GAP:
      if (now - timer >= gapMs) {
        currentTurn++;
        if (currentTurn >= totalTurns) {
          detachServoIfNeeded();
          feeding = false;
          state   = IDLE;
          Serial.println("[FEED] DONE → IDLE");
        } else {
          state = SERVO_OPEN;
        }
      }
      break;

    case IDLE:
    default:
      break;
  }
}
