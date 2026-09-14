# Wiring the first Nyx device

The code is based on the existing Testing project's ESP32-S3 and SH1106 constructor.
A "1.3-inch blue I2C OLED" label alone does not identify its controller. If the
tested screen is different, confirm its model before changing the constructor.

## Connections

Unplug USB before moving wires. Use the board's **3.3 V** supply and a common GND.
Do not connect a 5 V signal to an ESP32 GPIO.

| Part / signal | ESP32-S3 connection | Notes |
| --- | --- | --- |
| OLED VCC | 3V3 | Check the module's voltage rating |
| OLED GND | GND | Common ground |
| OLED SDA | GPIO 8 | Same as the test project |
| OLED SCK/SCL | GPIO 9 | SCK on this module means I2C clock |
| Approve button | GPIO 5 ↔ button ↔ GND | Existing button; press once |
| Reject button | GPIO 12 ↔ button ↔ GND | New second button |
| New-window button | GPIO 11 ↔ button ↔ GND | Opens a fresh Codex desktop task |
| MODE button | GPIO 4 ↔ button ↔ GND | Single press: effort; double press: model |
| Encoder CLK/A | GPIO 6 | Same as the test project |
| Encoder DT/B | GPIO 7 | Same as the test project |
| Encoder SW | GPIO 10 | Open session; confirm selection while in the MODE menu |
| Encoder GND | GND | Common ground |
| Encoder module + | 3V3 | Only if your module has a supply pin |

All input pins use internal pull-ups: released = HIGH, pressed = LOW.
A four-leg tactile button has two internally connected pairs. Use opposite
electrical sides, not two legs that are already connected. Check with a meter.

No LEDs, buzzer, potentiometer, or joystick need wiring for this version.
The OLED is monochrome blue; it cannot display RGB colors.

The MODE button is optional. Existing controls keep working without it. With a
live native session selected, press MODE once (wait about 320 ms) for effort or
twice quickly for model. Rotate the encoder to browse; press the encoder to
confirm. NO cancels the picker. Five seconds without input returns to the normal
screen without applying a browsing choice. Actual permissions always take priority.
Settings are for the next turn, and the menu waits for the native acknowledgement.

## USB

Use a data cable in the board's **USB/UART** connector—the same connector used
to upload the firmware. On the tested dual-USB board, macOS identifies its WCH
bridge as `USB Single Serial`. The port can change after reconnecting:
run `nyx ports` again. Do not choose Bluetooth-Incoming-Port or debug-console.

The PlatformIO target is the default 8 MB flash DevKitC-1 profile, without PSRAM.
Confirm your actual board model; this build does not depend on PSRAM.
A C3 Super Mini is not pin-compatible and cannot use this S3 build.

## First live test

1. Confirm wiring against `firmware/include/pins.h`, with power disconnected.
2. Flash only after confirming the board and serial port.
3. With no bridge running, the OLED should show BRIDGE OFFLINE.
4. Run Nyx in passive mode. With no sessions it should show NO SESSIONS OPEN.
5. Press NEW once: a fresh Codex desktop task should open. The encoder remains
   dedicated to selecting existing sessions.
6. Trust the installed hooks and start a Codex turn. Check RUNNING then IDLE.
7. Open two sessions; rotate to select, press the encoder to request focus.
8. Only then opt in to manual approvals and test one harmless real permission.
9. Unplug during a pending request: Codex must fall back, and reconnection must
   not allow the old button to approve anything.
10. Hold approve before a new request arrives: it must require release and a fresh press.

If the display stays blank, stop and check supply, ground, SDA/SCL, address
(default 0x3C), and the SH1106 model. For 0x3D change only OLED_ADDRESS in pins.h.
If rotation is reversed, swap ENCODER_A and ENCODER_B in pins.h.
Do not infer that a successful firmware build proves wiring is correct.
