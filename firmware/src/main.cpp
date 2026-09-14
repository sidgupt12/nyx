// Nyx device: USB messages + OLED + buttons. No Wi-Fi or Codex code here.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <U8g2lib.h>
#include <Wire.h>
#include "pins.h"
#include "personality.h"
#include "gestures.h"

U8G2_SH1106_128X64_NONAME_F_HW_I2C display(U8G2_R0, U8X8_PIN_NONE);

String viewId, status = "OFFLINE", project, session, preview;
String friendlyName, surface, model, effort;
String menuId, menuKind, menuValue, menuPhase, menuNote, notice;
int menuIndex = 0, menuCount = 0;
uint32_t menuTouched = 0, noticeAt = 0;
ModeGesture modeGesture;
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
Button newButton{NEW_BUTTON};
Button modeButton{MODE_BUTTON};

void resetButtons() {
  approve.reset();
  reject.reset();
  openButton.reset();
  newButton.reset();
  modeButton.reset();
  modeGesture.reset();
  menuId = "";
}

void sendMenu(const char *action, const char *kind = "") {
  if (!connected || viewId.isEmpty()) return;
  StaticJsonDocument<384> message;
  message["v"] = 1;
  message["type"] = "action";
  message["view_id"] = viewId;
  message["action"] = action;
  message["menu_id"] = menuId;
  message["kind"] = kind;
  serializeJson(message, Serial);
  Serial.println();
  menuTouched = millis();
}

void sendLaunch(const char *target) {
  if (!connected) return;
  StaticJsonDocument<192> message;
  message["v"] = 1;
  message["type"] = "action";
  message["action"] = "launch";
  message["target"] = target;
  serializeJson(message, Serial);
  Serial.println();
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
  if (message["v"] != 1) return;
  if (message["type"] == "result") {
    if (message["ok"] == false) {
      String error = message["error"] | "";
      if (error == "settings_not_ready" || error == "native_settings_unavailable")
        notice = "SETTINGS NOT READY";
      else if (error == "stale_menu") notice = "MENU EXPIRED";
      else if (error.startsWith("menu_")) notice = "MENU UNAVAILABLE";
      else return;
      noticeAt = millis();
    }
    return;
  }
  if (message["type"] != "state") return;
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
  String nextMenu = message["menu"]["id"] | "";
  if (status == "PERMISSION_REQUIRED") nextMenu = "";
  if (nextMenu != menuId) {
    menuTouched = millis();
    openButton.reset(); // A held encoder must not confirm a newly opened menu.
  }
  menuId = nextMenu;
  menuKind = readable(message["menu"]["kind"] | "");
  menuValue = readable(message["menu"]["value"] | "");
  menuPhase = readable(message["menu"]["phase"] | "");
  menuNote = readable(message["menu"]["note"] | "");
  menuIndex = message["menu"]["index"] | 0;
  menuCount = message["menu"]["count"] | 0;
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
  if (steps >= 4) {
    if (menuId.isEmpty()) sendAction("next"); else sendMenu("menu_next");
    steps = 0;
  }
  if (steps <= -4) {
    if (menuId.isEmpty()) sendAction("previous"); else sendMenu("menu_previous");
    steps = 0;
  }
}

void draw(uint32_t now) {
  display.clearBuffer();
  display.setFont(u8g2_font_6x10_tf);
  display.drawStr(0, 9, "NYX");
  display.setFont(u8g2_font_5x7_tf);
  if (connected && viewCount) {
    display.drawFrame(28, 0, 29, 11);
    display.drawStr(32, 8, surface == "TERM" ? "TERM" :
                               surface == "APP" ? "APP" : "?");
  }
  String counter = String(viewIndex) + "/" + String(viewCount);
  display.drawStr(128 - display.getStrWidth(counter.c_str()), 8, counter.c_str());
  display.drawHLine(0, 12, 128);
  display.setFont(u8g2_font_6x10_tf);
  if (!connected) {
    display.drawStr(0, 28, "BRIDGE OFFLINE");
    display.drawStr(0, 42, "WHERE'S MY HUMAN?");
    display.drawStr(0, 59, "Connect USB + run Nyx");
  } else if (!viewCount) {
    display.drawStr(0, 28, "SUMMON A SIDE QUEST");
    display.drawStr(0, 42, "NO SESSIONS OPEN");
    display.drawStr(0, 59, "Press NEW to launch");
  } else if (!menuId.isEmpty() && status != "PERMISSION_REQUIRED") {
    display.drawStr(0, 25, menuKind == "model" ? "CHOOSE MODEL" : "CHOOSE EFFORT");
    String value = menuValue;
    if (value.startsWith("gpt-")) value.remove(0, 4);
    display.drawStr(0, 39, value.substring(0, 21).c_str());
    display.setFont(u8g2_font_5x7_tf);
    display.drawStr(0, 50, menuPhase == "browse" ? "Rotate / push to set" : menuNote.c_str());
    display.drawHLine(0, 54, 128);
    if (menuPhase == "browse") {
      int seconds = max(0, 5 - int((now - menuTouched) / 1000));
      display.drawStr(0, 63, (String(menuIndex) + "/" + String(menuCount) +
                            "   Back in " + String(seconds) + "s").c_str());
    } else display.drawStr(0, 63, "No current turn changed");
  } else {
    display.drawStr(0, 24, friendlyName.substring(0, 18).c_str());
    if (status == "PERMISSION_REQUIRED") {
      display.setFont(u8g2_font_5x7_tf);
      if (actionable)
        display.drawStr(0, 33, nativeApproval ? "APPROVAL NEEDED" :
                        ("DECIDE " + String(remaining) + "s / OPEN").c_str());
      else
        display.drawStr(0, 33, "PERMISSION NEEDED");
      // Page the preview; never pretend a short OLED preview is the full command.
      size_t pages = max(size_t(1), (preview.length() + 41) / 42);
      size_t offset = ((now / 2500) % pages) * 42;
      display.setFont(u8g2_font_6x10_tf);
      display.drawStr(0, 42, preview.substring(offset, offset + 21).c_str());
      display.drawStr(0, 52, preview.substring(offset + 21, offset + 42).c_str());
      display.setFont(u8g2_font_5x7_tf);
      display.drawStr(0, 62, actionable ? "YES / NO / OPEN" : "Push knob: open");
    } else {
      // Keep the actual state readable alongside its cheeky caption.
      display.drawBox(0, 29, min(90, int(status.length()) * 6 + 8), 12);
      display.setDrawColor(0);
      display.drawStr(4, 38, status.substring(0, 13).c_str());
      display.setDrawColor(1);
      display.setFont(u8g2_font_5x7_tf);
      display.drawStr(0, 50, personality::caption(status));
      personality::buddy(display, 96, 28, now, status == "RUNNING",
                         model.endsWith("astra"));
      String runtime = model;
      if (runtime.startsWith("gpt-")) runtime.remove(0, 4);
      if (runtime.isEmpty()) runtime = "model ?";
      // Model and effort get independent columns: never truncate away effort.
      String level = effort.isEmpty() ? "?" : effort.substring(0, 6);
      int levelWidth = display.getStrWidth(level.c_str());
      int modelChars = (124 - levelWidth - 8) / 5;
      display.drawHLine(0, 55, 128);
      display.drawStr(0, 62, runtime.substring(0, modelChars).c_str());
      display.drawStr(128 - levelWidth, 62, level.c_str());
      if (!notice.isEmpty() && now - noticeAt < 2000) {
        display.setDrawColor(0);
        display.drawBox(0, 43, 96, 10);
        display.setDrawColor(1);
        display.drawStr(0, 50, notice.c_str());
      }
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
  newButton.begin();
  modeButton.begin();
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
  bool createNew = newButton.update(now);
  bool modePressed = modeButton.update(now);
  int modeClick = modeGesture.update(modePressed, now);
  if (connected) {
    if (!menuId.isEmpty() && now - menuTouched >= 5000) {
      sendMenu("menu_cancel");
      menuId = "";
      openButton.reset();
    }
    if (createNew) {
      modeGesture.reset();
      sendLaunch("codex");
    } else if (modeClick && status != "PERMISSION_REQUIRED") {
      sendMenu("menu_open", modeClick == 2 ? "model" : "effort");
    } else if (!menuId.isEmpty()) {
      if (no) sendMenu("menu_cancel");
      else if (open) sendMenu("menu_confirm");
    } else {
      // Simultaneous contradictory buttons never result in an approval.
      if (open) sendAction("open");
      else if (no && status == "PERMISSION_REQUIRED") sendAction("reject");
      else if (yes && digitalRead(REJECT_BUTTON) == HIGH &&
               digitalRead(OPEN_BUTTON) == HIGH && status == "PERMISSION_REQUIRED")
        sendAction("approve");
    }
    readEncoder();
  }
  if (now - lastDraw >= 150) { draw(now); lastDraw = now; }
}
