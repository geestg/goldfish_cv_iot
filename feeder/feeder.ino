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
#define SERVO_PIN 18
#define SERVO_CLOSE_US 500
#define SERVO_OPEN_US  2400

Servo feeder;

/* ================= MQTT ================= */
WiFiClient espClient;
PubSubClient client(espClient);

/* ================= FEED STATE ================= */
bool feedingActive = false;
int totalTurns = 0;
int currentTurn = 0;

int openTimeMs = 700;
int gapTimeMs  = 600;

unsigned long stateTimer = 0;

/* ================= FSM ================= */
enum FeedState {
  IDLE,
  OPEN,
  WAIT_OPEN,
  WAIT_GAP
};

FeedState feedState = IDLE;

/* ================= SERVO ================= */
void servoOpen()  { feeder.writeMicroseconds(SERVO_OPEN_US); }
void servoClose() { feeder.writeMicroseconds(SERVO_CLOSE_US); }

/* ================= MQTT CALLBACK ================= */
void callback(char* topic, byte* payload, unsigned int length) {
  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, payload, length)) return;

  if (strcmp(doc["action"] | "", "feed") != 0) return;
  if (feedingActive) return;

  totalTurns = doc["turns"] | 1;
  openTimeMs = doc["duration"] | 700;
  gapTimeMs  = doc["gap"] | 600;

  currentTurn = 0;
  feedingActive = true;
  feedState = OPEN;

  Serial.printf("FEED START | turns=%d\n", totalTurns);
}

/* ================= FSM PROCESS ================= */
void processFeeding() {
  if (!feedingActive) return;

  unsigned long now = millis();

  switch (feedState) {

    case OPEN:
      servoOpen();
      stateTimer = now;
      feedState = WAIT_OPEN;
      break;

    case WAIT_OPEN:
      if (now - stateTimer >= (unsigned long)openTimeMs) {
        servoClose();
        stateTimer = now;
        feedState = WAIT_GAP;
      }
      break;

    case WAIT_GAP:
      if (now - stateTimer >= (unsigned long)gapTimeMs) {
        currentTurn++;
        Serial.printf("TURN %d / %d\n", currentTurn, totalTurns);

        if (currentTurn >= totalTurns) {
          feedingActive = false;
          feedState = IDLE;
          Serial.println("FEED DONE");
        } else {
          feedState = OPEN;
        }
      }
      break;

    case IDLE:
    default:
      break;
  }
}

/* ================= MQTT CONNECT ================= */
void connectMQTT() {
  while (!client.connected()) {
    if (client.connect("ESP32_FEEDER")) {
      client.subscribe(TOPIC_CMD);
      Serial.println("MQTT Connected");
    } else {
      delay(1000);
    }
  }
}

/* ================= SETUP ================= */
void setup() {
  Serial.begin(115200);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(300);
  Serial.println("WiFi connected");

  client.setServer(MQTT_BROKER, MQTT_PORT);
  client.setCallback(callback);

  feeder.setPeriodHertz(50);
  feeder.attach(SERVO_PIN, 500, 2500);
  servoClose();

  Serial.println("ESP32 FEEDER READY");
}

/* ================= LOOP ================= */
void loop() {
  if (!client.connected()) connectMQTT();
  client.loop();
  processFeeding();
}
