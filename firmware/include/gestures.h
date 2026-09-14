#pragma once
#include <stdint.h>

// Input is one debounced press edge, not a raw pin. No delays or extra timer.
struct ModeGesture {
  bool waiting = false;
  uint32_t firstAt = 0;
  void reset() { waiting = false; }
  // 1 = effort, 2 = model. Single click waits for the double-click window.
  int update(bool pressed, uint32_t now) {
    int result = 0;
    if (waiting && now - firstAt > 320) { waiting = false; result = 1; }
    if (pressed) {
      if (waiting) { waiting = false; return 2; }
      waiting = true;
      firstAt = now;
    }
    return result;
  }
};
