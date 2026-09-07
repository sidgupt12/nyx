// Nyx device: USB messages + OLED + buttons. No Wi-Fi or Codex code here.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <U8g2lib.h>
#include <Wire.h>
#include "pins.h"

U8G2_SH1106_128X64_NONAME_F_HW_I2C display(U8G2_R0, U8X8_PIN_NONE);

String viewId, status = "OFFLINE", project, session, preview;
String friendlyName, surface, model, effort;
int remaining = 0, viewIndex = 0, viewCount = 0;
bool connected = false;
bool actionable = false;
bool nativeApproval = false;
uint32_t lastState = 0, lastHeartbeat = 0, lastDraw = 0;
char line[4097];
size_t lineSize = 0;
bool droppingLine = false;

// Polling debounce: no delay() while waiting for a button or USB message.
struct Button {
  int pin;
  explicit Button(int gpio) : pin(gpio) {}
  bool raw = HIGH, stable = HIGH, armed = false, sent = false;
  uint32_t changedAt = 0, pressedAt = 0;

  void begin() { pinMode(pin, INPUT_PULLUP); }
  void reset() { armed = false; sent = true; }

  bool update(uint32_t now, uint32_t holdMs = 0) {
    bool value = digitalRead(pin);
    if (value != raw) { raw = value; changedAt = now; }
    if (now - changedAt < 30) return false;
    if (stable != raw) {
      stable = raw;
      if (stable == LOW) { pressedAt = now; sent = false; }
    }
    if (stable == HIGH) { armed = true; return false; }
    if (armed && !sent && now - pressedAt >= holdMs) {
      sent = true;
      armed = false;
      return true;
    }
    return false;
  }
};

Button approve{APPROVE_BUTTON}, reject{REJECT_BUTTON}, openButton{OPEN_BUTTON};

void resetButtons() {
  approve.reset();
  reject.reset();
  openButton.reset();
}

void sendAction(const char *action) {
  if (!connected || viewId.isEmpty()) return;
  StaticJsonDocument<256> message;
  message["v"] = 1;
  message["type"] = "action";
  message["action"] = action;
  message["view_id"] = viewId;
  serializeJson(message, Serial);
  Serial.println();
}

// The OLED font is ASCII; replace unsupported/control bytes instead of
// letting tool output alter the layout. Full details stay in Codex.
String readable(const char *input) {
  String output;
  for (size_t i = 0; input[i] && i < 960; ++i) {
    unsigned char c = input[i];
    output += (c >= 32 && c < 127) ? char(c) : ' ';
  }
  return output;
}

void acceptLine() {
  StaticJsonDocument<4096> message;
  if (deserializeJson(message, line, lineSize)) return;
  if (message["v"] != 1 || message["type"] != "state") return;
  if (!message["view_id"].is<const char *>() ||
      !message["status"].is<const char *>()) return;
  String nextView = message["view_id"].as<String>();
  if (nextView.length() > 128) return;
  if (nextView != viewId || !connected) resetButtons();
  viewId = nextView;
  status = message["status"].as<String>();
  project = readable(message["project"] | "");
  session = readable(message["session"] | "");
  friendlyName = readable(message["name"] | "Codex Session");
  surface = readable(message["surface"] | "");
  model = readable(message["model"] | "");
  effort = readable(message["effort"] | "");
  preview = readable(message["detail"] | "");
  remaining = message["remaining"] | 0;
  actionable = message["actionable"] | false;
  nativeApproval = message["native"] | false;
  viewIndex = message["index"] | 0;
  viewCount = message["count"] | 0;
  connected = true;
  lastState = millis();
}

void readUsb() {
  // Bound work per loop so a noisy sender cannot starve button polling.
  for (int budget = 0; Serial.available() && budget < 512; ++budget) {
    char c = char(Serial.read());
    if (c == '\n') {
      if (!droppingLine) { line[lineSize] = '\0'; acceptLine(); }
      lineSize = 0;
      droppingLine = false;
    } else if (!droppingLine) {
      if (lineSize < sizeof(line) - 1) line[lineSize++] = c;
      else { lineSize = 0; droppingLine = true; }
    }
  }
}

void readEncoder() {
  // A complete quadrature cycle, not one contact bounce, moves one session.
  static uint8_t previous = 0;
  static int steps = 0;
  static const int8_t transitions[] = {0,-1,1,0, 1,0,0,-1, -1,0,0,1, 0,1,-1,0};
  uint8_t current = (digitalRead(ENCODER_A) << 1) | digitalRead(ENCODER_B);
  steps += transitions[(previous << 2) | current];
  previous = current;
  if (steps >= 4) { sendAction("next"); steps = 0; }
  if (steps <= -4) { sendAction("previous"); steps = 0; }
}

void draw(uint32_t now) {
  display.clearBuffer();
  display.setFont(u8g2_font_6x10_tf);
  display.drawStr(0, 9, "NYX");
  display.setCursor(88, 9);
  display.print(String(viewIndex) + "/" + String(viewCount));
  display.drawHLine(0, 12, 128);
  if (!connected) {
    display.drawStr(0, 28, "BRIDGE OFFLINE");
    display.drawStr(0, 43, "Connect USB + run Nyx");
  } else if (!viewCount) {
    display.drawStr(0, 28, "WAITING FOR CODEX");
    display.drawStr(0, 43, "Start a trusted hook");
  } else {
    // The display font has no Unicode emoji, so these tiny pixel icons are
    // sharper and more reliable: a window for App, a >_ prompt for Terminal.
    if (surface == "APP") {
      display.drawFrame(0, 17, 12, 9);
      display.drawHLine(1, 19, 10);
      display.drawPixel(2, 18);
      display.drawPixel(4, 18);
    } else {
      display.drawFrame(0, 17, 12, 9);
      display.drawLine(2, 20, 4, 22);
      display.drawLine(4, 22, 2, 24);
      display.drawHLine(6, 24, 3);
    }
    display.drawStr(16, 25, friendlyName.substring(0, 18).c_str());
    if (status == "PERMISSION_REQUIRED") {
      if (actionable)
        display.drawStr(0, 36, nativeApproval ? "DECIDE / OPEN" :
                        ("DECIDE " + String(remaining) + "s / OPEN").c_str());
      else
        display.drawStr(0, 36, "PERMISSION NEEDED");
      // Page the preview; never pretend a short OLED preview is the full command.
      size_t pages = max(size_t(1), (preview.length() + 41) / 42);
      size_t offset = ((now / 2500) % pages) * 42;
      display.drawStr(0, 47, preview.substring(offset, offset + 21).c_str());
      display.drawStr(0, 57, preview.substring(offset + 21, offset + 42).c_str());
      display.drawStr(0, 64, actionable ? "YES / NO / OPEN" : "Push knob: open");
    } else {
      display.drawStr(0, 43, status.c_str());
      String runtime = model;
      if (runtime.startsWith("gpt-")) runtime.remove(0, 4);
      if (!effort.isEmpty()) runtime += " / " + effort;
      display.drawStr(0, 59, runtime.substring(0, 21).c_str());
    }
  }
  display.sendBuffer();
}

void setup() {
  Serial.begin(115200);
  // Never wait for Serial: the device must boot even without the Mac.
  Wire.begin(OLED_SDA, OLED_SCL);
  Wire.setTimeOut(30);
  display.setI2CAddress(OLED_ADDRESS * 2); // U8g2 expects the shifted address.
  display.begin();
  approve.begin();
  reject.begin();
  openButton.begin();
  pinMode(ENCODER_A, INPUT_PULLUP);
  pinMode(ENCODER_B, INPUT_PULLUP);
}

void loop() {
  readUsb();
  uint32_t now = millis();
  if (connected && now - lastState > 3000) {
    connected = false;
    viewId = "";
    resetButtons();
  }
  if (now - lastHeartbeat >= 1000) {
    Serial.println(connected ? "{\"v\":1,\"type\":\"heartbeat\"}"
                             : "{\"v\":1,\"type\":\"hello\"}");
    lastHeartbeat = now;
  }
  bool yes = approve.update(now);
  bool no = reject.update(now);
  bool open = openButton.update(now);
  if (connected) {
    // Simultaneous contradictory buttons never result in an approval.
    if (open) sendAction("open");
    else if (no && status == "PERMISSION_REQUIRED") sendAction("reject");
    else if (yes && digitalRead(REJECT_BUTTON) == HIGH &&
             digitalRead(OPEN_BUTTON) == HIGH && status == "PERMISSION_REQUIRED")
      sendAction("approve");
    readEncoder();
  }
  if (now - lastDraw >= 150) { draw(now); lastDraw = now; }
}
