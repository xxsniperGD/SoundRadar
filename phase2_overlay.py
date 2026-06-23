"""Phase 2 demo — prove the click-through border overlay works.

Runs the transparent always-on-top overlay with a hardcoded animation that
sweeps brightness around the 7.1 regions (FL -> C -> FR -> SR -> RR -> RL ->
SL -> LFE). No audio yet. Use this to confirm, over a game in BORDERLESS
WINDOWED mode:
  * the border is visible on top of the game
  * mouse/keyboard clicks pass THROUGH to the game (click-through works)

There is no visible window to close it (it's click-through), so it exits
automatically after --seconds, or press Ctrl+C in this console.

Usage:
    python phase2_overlay.py
    python phase2_overlay.py --seconds 60 --thickness 8 --glow 60
"""

from __future__ import annotations

import argparse
import math
import signal

from PySide6 import QtCore, QtWidgets

from soundradar.overlay import OverlayWindow, OverlayStyle


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30.0,
                    help="auto-close after N seconds (default 30)")
    ap.add_argument("--thickness", type=int, default=6)
    ap.add_argument("--glow", type=int, default=48)
    args = ap.parse_args()

    app = QtWidgets.QApplication([])
    overlay = OverlayWindow(OverlayStyle())
    overlay.show()

    # sweep a bright spot around the compass, length pulsing with loudness
    order = ["C", "FR", "SR", "RR", "RL", "SL", "FL"]
    state = {"t": 0.0}

    def tick():
        state["t"] += 0.04
        t = state["t"]
        vals = {}
        pos = (t * 1.2) % len(order)
        for i, ch in enumerate(order):
            d = min(abs(i - pos), len(order) - abs(i - pos))
            vals[ch] = max(0.0, 1.0 - d / 1.5)
        overlay.set_channel_intensities(vals, 0.2 + 0.2 * math.sin(t * 2))

    timer = QtCore.QTimer()
    timer.timeout.connect(tick)
    timer.start(16)  # ~60 fps

    QtCore.QTimer.singleShot(int(args.seconds * 1000), app.quit)
    # allow Ctrl+C to fall through to Python
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    timer_kick = QtCore.QTimer()
    timer_kick.timeout.connect(lambda: None)
    timer_kick.start(200)

    print(f"Overlay running for {args.seconds:.0f}s. "
          f"Click around — input should reach whatever is underneath.")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
