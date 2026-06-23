"""Phase 1 console monitor — prove loopback capture works.

Run this WHILE a game (or any surround source) is playing and watch the
per-channel RMS bars. The header prints how many channels actually arrive,
which is the key thing to confirm: are we getting true 7.1 (8 channels) or a
collapsed 2-channel stereo/binaural mix?

Usage:
    python monitor.py              # default speaker loopback
    python monitor.py --list       # list loopback devices and exit
    python monitor.py --device "Speakers (Realtek...)"
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from soundradar.audio import (
    CaptureConfig,
    LoopbackCapture,
    list_loopback_devices,
    rms_to_dbfs,
)

BAR_WIDTH = 28


def bar(value01: float) -> str:
    n = int(round(max(0.0, min(1.0, value01)) * BAR_WIDTH))
    return "#" * n + "-" * (BAR_WIDTH - n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true",
                    help="list loopback devices and exit")
    ap.add_argument("--device", default=None, help="loopback device name")
    ap.add_argument("--floor-db", type=float, default=-60.0,
                    help="dBFS mapped to an empty bar (default -60)")
    args = ap.parse_args()

    if args.list:
        print("Loopback devices:")
        for m in list_loopback_devices():
            print(f"  channels={m.channels:2}  {m.name}")
        return 0

    cfg = CaptureConfig(device_name=args.device)
    cap = LoopbackCapture(cfg)
    cap.start()
    print("Capturing... play some surround audio. Ctrl+C to stop.\n")

    floor = args.floor_db
    try:
        while True:
            lv = cap.get_levels()
            if lv.channels == 0:
                time.sleep(0.1)
                continue
            db = rms_to_dbfs(lv.rms)
            # map [floor..0] dBFS -> [0..1]
            norm = np.clip((db - floor) / (0.0 - floor), 0.0, 1.0)

            lines = [f"channels: {lv.channels}   (Ctrl+C to stop)"]
            for label, d, nv in zip(lv.labels, db, norm):
                lines.append(f"  {label:>3}  {bar(nv)}  {d:6.1f} dBFS")
            out = "\n".join(lines)
            # redraw in place
            sys.stdout.write("\033[H\033[J" + out + "\n")
            sys.stdout.flush()
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        cap.stop()
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
