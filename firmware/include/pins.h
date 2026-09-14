#pragma once

// GPIO numbers, not physical header positions. Based on your Testing project.
// Buttons connect to GND and use the ESP32's internal pull-up resistors.
constexpr int OLED_SDA = 8;
constexpr int OLED_SCL = 9;
constexpr int OLED_ADDRESS = 0x3C;
constexpr int APPROVE_BUTTON = 5;  // Your existing approve button.
constexpr int REJECT_BUTTON = 12; // Reject/cancel button.
constexpr int OPEN_BUTTON = 10;   // Existing encoder push-switch.
constexpr int NEW_BUTTON = 11;    // New-session button; other side goes to GND.
constexpr int MODE_BUTTON = 4;    // Effort/model picker; other side goes to GND.
constexpr int ENCODER_A = 6;
constexpr int ENCODER_B = 7;
