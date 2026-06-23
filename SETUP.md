# SoundRadar — surround setup checklist

Full front/back/side radar needs the game to output real 7.1 into a virtual
surround device. Some of this **resets when you reboot**, so run through it each
session (until we automate it). Your hearing is carried by SoundRadar's mono
mix, so Windows "Mono audio" must be OFF for this mode.

## One-time (survives reboot)
- VoiceMeeter Potato installed.
- In **each game's** Windows output: Settings → System → Sound → **Volume mixer**
  → set the game's **Output device** to **Voicemeeter VAIO3 Input**.
  (Windows pins this per app; do it once per game while it's running.)
- Optional: on the game's `.exe` → Properties → Compatibility →
  ☑ **Disable fullscreen optimizations** (helps the overlay show).

## Each session (after reboot)
1. **Launch VoiceMeeter.**
2. **Sound control panel** (`Win+R` → `mmsys.cpl`): select **Voicemeeter VAIO3
   Input** → **Configure** → **7.1 Surround** → Finish. Then **Set Default**.
3. In **VoiceMeeter**: make sure **A1 is empty** (click A1 → blank). SoundRadar
   plays your audio, not VoiceMeeter.
4. **Turn Windows "Mono audio" OFF** (`Win+U` → Audio). SoundRadar does the mono
   mix for your ear instead.
5. **Start SoundRadar** (desktop icon, or):
   ```
   python run.py --route-audio --device "Voicemeeter VAIO3 Input" --gain 0.9
   ```
6. Game in **Borderless Windowed**. Don't change sound settings while playing
   (it resets the audio engine).

## If something breaks
- No sound / stuck audio: close SoundRadar + VoiceMeeter, set default back to
  **Headphones**; if still stuck, **reboot** (clears it completely).
- Overlay reacts to other audio but not the game: the game's output device isn't
  VAIO3 — fix it in Volume mixer (step under "One-time").

## Clean stereo mode (no VoiceMeeter, left/right only)
Zero audio changes, Windows Mono can stay ON, but no front/back:
```
python run.py --all-apps          # or --process stalker2
```
