/*
 * EasyPlay BLE UART Remote v6 (Arduino / ESP32-C3 SuperMini)
 *
 * Minimal version — matches S3 v7 BLE handshake exactly.
 * No sleep, just BLE UART + buttons + NeoPixel LED.
 *
 * GPIO (active LOW, INPUT_PULLUP — buttons wired to GND):
 *   GPIO 0 = Left
 *   GPIO 1 = Down
 *   GPIO 2 = Right
 *   GPIO 3 = Up
 *   GPIO 4 = On/Off
 *
 * NeoPixel: GPIO 8
 *
 * Button codes: uppercase = press, lowercase = release
 *   L/l  R/r  U/u  D/d  O/o
 */

#include <NimBLEDevice.h>
#include <Adafruit_NeoPixel.h>

// ── NeoPixel ─────────────────────────────────────────────────────────────────
#define NEOPIXEL_PIN 8
Adafruit_NeoPixel pixel(1, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);

// ── Buttons ──────────────────────────────────────────────────────────────────
struct Button {
  uint8_t pin;
  char    downChar;
  char    upChar;
  const char* label;
  bool    prevPressed;
  unsigned long lastChangeMs;
};

Button buttons[] = {
  { 0, 'L', 'l', "LEFT",   false, 0 },
  { 1, 'D', 'd', "DOWN",   false, 0 },
  { 2, 'R', 'r', "RIGHT",  false, 0 },
  { 3, 'U', 'u', "UP",     false, 0 },
  { 4, 'O', 'o', "ON/OFF", false, 0 },
};
const int NUM_BUTTONS = sizeof(buttons) / sizeof(buttons[0]);
const unsigned long DEBOUNCE_MS = 50;

// ── BLE NUS UUIDs (Nordic UART Service) ──────────────────────────────────────
#define SERVICE_UUID "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define TX_UUID      "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  // notify to Pi
#define RX_UUID      "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  // write from Pi

// ── Globals ──────────────────────────────────────────────────────────────────
NimBLEServer* pServer = nullptr;
NimBLECharacteristic* pTxChar = nullptr;
bool deviceConnected = false;
bool wasConnected = false;
unsigned long connectedAt = 0;

// ── BLE Callbacks ────────────────────────────────────────────────────────────
class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo) override {
    deviceConnected = true;
    connectedAt = millis();
    Serial.println("Pi connected!");
    // Green LED
    pixel.setPixelColor(0, pixel.Color(0, 25, 0));
    pixel.show();
  }

  void onDisconnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo, int reason) override {
    deviceConnected = false;
    Serial.printf("Disconnected (reason=%d)\n", reason);
    // LED off
    pixel.setPixelColor(0, 0);
    pixel.show();
    // Restart advertising
    NimBLEDevice::startAdvertising();
    Serial.println("Advertising restarted");
  }
};

class RxCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* pChar, NimBLEConnInfo& connInfo) override {
    std::string val = pChar->getValue();
    if (val.length() > 0) {
      Serial.printf("RX from Pi: %s\n", val.c_str());
    }
  }
};

// ── Send a character via BLE notify ──────────────────────────────────────────
bool bleSend(char c) {
  if (!deviceConnected || pTxChar == nullptr) return false;
  uint8_t data = (uint8_t)c;
  pTxChar->setValue(&data, 1);
  pTxChar->notify();
  return true;
}

// ── Setup ────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(2000);  // safety window for serial
  Serial.println("EasyPlay Remote C3 v6 (Arduino) starting...");

  // NeoPixel init
  pixel.begin();
  pixel.setBrightness(255);  // we control brightness via color values
  pixel.setPixelColor(0, 0);
  pixel.show();

  // Button pins
  for (int i = 0; i < NUM_BUTTONS; i++) {
    pinMode(buttons[i].pin, INPUT_PULLUP);
  }

  // ── BLE init ─────────────────────────────────────────────────────────────
  NimBLEDevice::init("EasyPlay");

  // Force public address — Pi's Bleak connects by MAC expecting public type
  NimBLEDevice::setOwnAddrType(BLE_OWN_ADDR_PUBLIC);

  // Print MAC
  Serial.printf("MAC: %s\n", NimBLEDevice::getAddress().toString().c_str());

  // Create server
  pServer = NimBLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  // Create NUS service
  NimBLEService* pService = pServer->createService(SERVICE_UUID);

  // TX characteristic — notify to Pi (matches S3 v7: READ | NOTIFY)
  pTxChar = pService->createCharacteristic(
    TX_UUID,
    NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY
  );

  // RX characteristic — write from Pi (matches S3 v7: WRITE_NR)
  NimBLECharacteristic* pRxChar = pService->createCharacteristic(
    RX_UUID,
    NIMBLE_PROPERTY::WRITE_NR
  );
  pRxChar->setCallbacks(new RxCallbacks());

  pService->start();

  // ── Advertising ────────────────────────────────────────────────────────
  NimBLEAdvertising* pAdvertising = NimBLEDevice::getAdvertising();
  pAdvertising->setName("EasyPlay");
  // Fast advertising interval: 250ms (match S3 v7's gap_advertise(250_000))
  // NimBLE uses 0.625ms units: 250ms / 0.625 = 400
  pAdvertising->setMinInterval(400);
  pAdvertising->setMaxInterval(400);
  // Add service UUID so BlueZ can identify the device type
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->start();

  Serial.println("Advertising as 'EasyPlay' — waiting for connection...");
  // Brief blue flash to show boot
  pixel.setPixelColor(0, pixel.Color(0, 0, 25));
  pixel.show();
  delay(200);
  pixel.setPixelColor(0, 0);
  pixel.show();
}

// ── Main loop ────────────────────────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  // Connected LED: fade green to off after 3s
  if (deviceConnected && wasConnected) {
    unsigned long elapsed = now - connectedAt;
    if (elapsed > 3500) {
      pixel.setPixelColor(0, 0);
      pixel.show();
    }
  }

  // Track connection state changes
  if (deviceConnected && !wasConnected) {
    wasConnected = true;
  }
  if (!deviceConnected && wasConnected) {
    wasConnected = false;
  }

  // ── Button scanning ──────────────────────────────────────────────────────
  for (int i = 0; i < NUM_BUTTONS; i++) {
    bool pressed = digitalRead(buttons[i].pin) == LOW;  // active LOW

    if (pressed != buttons[i].prevPressed) {
      if ((now - buttons[i].lastChangeMs) > DEBOUNCE_MS) {
        if (pressed) {
          Serial.printf("BTN DOWN: %s\n", buttons[i].label);
          if (deviceConnected) {
            bleSend(buttons[i].downChar);
          }
        } else {
          Serial.printf("BTN UP:   %s\n", buttons[i].label);
          if (deviceConnected) {
            bleSend(buttons[i].upChar);
          }
        }
        buttons[i].lastChangeMs = now;
        buttons[i].prevPressed = pressed;
      }
    }
  }

  delay(10);
}
