#pragma once

#include <Arduino.h>
#include <U8g2lib.h>

// Presentation only. Protocol states, model IDs and approval actions stay literal.
namespace personality {

inline const char *caption(const String &status) {
  if (status == "RUNNING") return "LORE IN PROGRESS";
  if (status == "IDLE") return "AURA RESTORED";
  return "";
}

// Original 32x24 pixel familiar. All coordinates stay within this box.
// Cosmetic only; uses the existing redraw clock, no extra timer or dependency.
inline void buddy(U8G2 &d, int x, int y, uint32_t now,
                  bool cooking, bool astra) {
  bool blink = now % 4800 < 150;
  d.drawLine(x + 5, y + 8, x + 7, y + 3);
  d.drawLine(x + 7, y + 3, x + 12, y + 7);
  d.drawHLine(x + 12, y + 7, 8);
  d.drawLine(x + 19, y + 7, x + 24, y + 3);
  d.drawLine(x + 24, y + 3, x + 26, y + 8);
  d.drawVLine(x + 5, y + 8, 11);
  d.drawVLine(x + 26, y + 8, 11);
  d.drawLine(x + 5, y + 18, x + 9, y + 22);
  d.drawLine(x + 9, y + 22, x + 13, y + 19);
  d.drawLine(x + 13, y + 19, x + 18, y + 22);
  d.drawLine(x + 18, y + 22, x + 22, y + 19);
  d.drawLine(x + 22, y + 19, x + 26, y + 18);
  if (astra) {
    d.drawLine(x + 12, y + 3, x + 11, y);
    d.drawLine(x + 11, y, x + 15, y + 2);
    d.drawLine(x + 15, y + 2, x + 19, y);
    d.drawLine(x + 19, y, x + 18, y + 3);
    d.drawHLine(x + 12, y + 3, 7);
    d.drawHLine(x + 7, y + 10, 18);
    d.drawBox(x + 8, y + 11, 6, 4);
    d.drawBox(x + 18, y + 11, 6, 4);
  } else if (blink || !cooking) {
    d.drawHLine(x + 9, y + 13, 4);
    d.drawHLine(x + 19, y + 13, 4);
  } else {
    d.drawLine(x + 8, y + 10, x + 13, y + 12);
    d.drawLine(x + 18, y + 12, x + 23, y + 10);
    d.drawBox(x + 11, y + 12, 2, 3);
    d.drawBox(x + 19, y + 12, 2, 3);
  }
  d.drawHLine(x + 14, y + 17, 4);
  if (cooking) {
    const int sparkY = 5 + (now / 300) % 12;
    d.drawVLine(x + 1, y + sparkY, 3);
    d.drawHLine(x, y + sparkY + 1, 3);
    d.drawPixel(x + 30, y + 21 - sparkY);
  }
}

} // namespace personality
