"""SoundRadar capture diagnostic.

Shows what audio SoundRadar is receiving from your configured capture device,
so you can answer two questions:

  1. Is ANY audio arriving?       (line shows "...silence..." = nothing)
  2. REAL surround or mono?       (spread small = collapsed to mono)

Run:  python diag.py
Play a game / video with clearly directional sound while it runs.
It prints one line ~twice a second and stops itself after ~90 seconds
(or press Ctrl-C anytime).
"""

from __future__ import annotations

import sys
import time

import numpy as np
import soundcard as sc

from soundradar import settings as settings_mod
from soundradar.audio import labels_for, rms_to_dbfs

SR = 48000
BLOCK = 2400      # 50 ms frames
PRINT_EVERY = 0.5  # seconds between printed lines
RUN_SECONDS = 90.0


def main() -> None:
    s = settings_mod.load()
    print(f"mode            : {s.mode}")
    print(f"capture_device  : {s.capture_device or '(default speaker loopback)'}")
    print()

    print("=== Loopback devices available ===")
    for m in sc.all_microphones(include_loopback=True):
        loop = " [loopback]" if getattr(m, "isloopback", False) else ""
        print(f"  ch={m.channels:<2} {m.name}{loop}")
    print()

    name = s.capture_device or None
    try:
        if name is None:
            spk = sc.default_speaker()
            mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
        else:
            mic = sc.get_microphone(id=name, include_loopback=True)
    except Exception as e:  # noqa: BLE001
        print(f"!! Could not open '{name}': {e}")
        sys.exit(1)

    channels = mic.channels
    labels = labels_for(channels)
    print(f"Capturing '{mic.name}'  ({channels} channels)")
    print("Play directional sound. Each line shows the loudest channels.\n")

    peak = np.zeros(channels, dtype=np.float64)
    last_print = 0.0
    start = time.perf_counter()

    with mic.recorder(samplerate=SR, channels=channels, blocksize=BLOCK) as rec:
        try:
            while time.perf_counter() - start < RUN_SECONDS:
                data = rec.record(numframes=BLOCK)
                if data.size == 0:
                    continue
                rms = np.sqrt(np.mean(np.square(data, dtype=np.float64), axis=0))
                peak = np.maximum(peak * 0.5, rms)

                now = time.perf_counter()
                if now - last_print < PRINT_EVERY:
                    continue
                last_print = now

                db = rms_to_dbfs(peak.astype(np.float32))
                active = db > -55.0
                if not active.any():
                    print("  ...silence (no audio on this device)...")
                    continue

                spread = float(db[active].max() - db[active].min())
                # list only channels that are making noise, loudest first
                idx = np.argsort(db)[::-1]
                parts = []
                for i in idx:
                    if db[i] <= -55.0:
                        continue
                    lab = labels[i] if i < len(labels) else f"ch{i}"
                    parts.append(f"{lab}={db[i]:.0f}")
                tag = "SURROUND" if spread > 6.0 else "mono/uniform"
                print(f"  [{tag:>12}] spread={spread:4.1f}dB  " + "  ".join(parts))
        except KeyboardInterrupt:
            pass
    print("\nstopped.")


if __name__ == "__main__":
    main()
