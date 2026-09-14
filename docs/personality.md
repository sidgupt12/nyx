# Nyx has opinions

Voice: a mischievous desk companion. Dry little jokes, readable at a glance.
One joke per screen; actual states, model names and decisions stay legible.

## Implemented

- Character-style names: Momo, Zuzu, Gizmo, Mochi, Ziggy and Pingu.
  No slogan-style session names. Stored in `nyx/session_info.py`.
- A compact APP/TERM badge and right-aligned session counter in the header.
- A full-width name row; an inverted status badge to separate state from jokes.
- RUNNING + LORE IN PROGRESS; IDLE + AURA RESTORED.
- No sessions: SUMMON A SIDE QUEST / NO SESSIONS OPEN / Press NEW to launch.
- Disconnected: BRIDGE OFFLINE / WHERE'S MY HUMAN? / Connect USB + run Nyx.
- An original 32x24 horned pixel ghost beside the state. Astra wears a crown
  and shades; running sessions get travelling sparks. Drawn directly in C++
  in `firmware/include/personality.h`, with no extra animation thread.
- Model and effort occupy separate footer columns, so long model names cannot
  push the effort off-screen. Unknown values show `?`, not guessed settings.
- Permission screens keep the command preview and clear YES / NO / OPEN controls.

The new alias list changes existing aliases once when the bridge restarts.
Aliases are nicknames, not unique IDs; all actions still use the real session ID.
All 64 aliases are checked for uniqueness, ASCII and the 18-character limit.
This is a firmware/source change, not evidence that the device has been flashed.

Empty new windows are not yet tracked. See [the isolated lifecycle plan](empty-window-tracking.md).

## Model/effort menu (implemented; physical validation pending)

The interaction: MODE once for effort, twice for model, encoder
to browse and press to confirm. Cancel after five seconds without interaction.
Browsing must never change a setting; confirmation must round-trip through the
bridge before showing a success acknowledgement. GPIO4 is the MODE input.
Current menus use literal model/effort values. The following reactions are
future artwork ideas, not implemented behaviour:

Effort captions beside the real setting:

| Actual setting | Caption | Pixel reaction |
| --- | --- | --- |
| Low | LIGHT WORK | Half-asleep buddy |
| Medium | LOCKING IN | Eyes open |
| High | LET HIM COOK | Tiny flame |
| Xhigh | LORE DEEP | Expanding brain |
| Max | FINAL BOSS | Shades + aura |
| Ultra | REALITY CHECK | Brain escapes frame |

Show only levels reported as supported. Captions are jokes, not capability claims.
Possible model cards: Astra / BOSS MUSIC, Sol / OL' RELIABLE,
Terra / TOUCH GRASS, Luna / NIGHT SHIFT. Unknown models keep their real label.

A Sam Altman/Chad caricature could be a later custom monochrome sprite.
The current buddy is original geometric pixel art, not a portrait of Sam.
The OLED is 128x64 monochrome blue: pixels are on/off, not RGB. A 32x32 image
uses 128 bytes. A converted XBM byte array can live in flash and be drawn with
U8g2's drawXBMP; C++ displays those pixels rather than generating a photograph.
Prefer brief reactions after a confirmed selection. Never cover a pending
permission, pretend IDLE means success, or show "approved" before acknowledgement.
