"""Transparent, always-on-top, click-through directional radar overlay.

The screen border is a compass around the player:
    top    = front      bottom = behind
    left   = left        right  = right
Each sound lights a band that lies FLAT against the screen edge at its
direction and GROWS ALONG the edge as it gets louder/closer — like a game's
directional threat/detection indicator. A soft/distant sound is a short stub
hugging the edge; a loud/near one spreads further around the border. Direction
is position; loudness is how far the band spreads (plus brightness).

Works with 7.1 (full front/back/side) or stereo (left/right only).
Click-through is mandatory (WS_EX_TRANSPARENT) so input reaches the game.
"""

from __future__ import annotations

import ctypes
import math
from ctypes import wintypes
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

# Channel -> compass angle in degrees, clockwise from the TOP (front).
# The 7 channels are spread evenly (360/7) so they aren't bunched at the top;
# order around the ring is C, FR, SR, RR, RL, SL, FL.
CHANNEL_ANGLES = {
    "C": 0.0,
    "FR": 51.4, "SR": 102.9, "RR": 154.3,
    "RL": 205.7, "SL": 257.1, "FL": 308.6,
    "L": 270.0, "R": 90.0,   # stereo fallback
}

# --- Win32 click-through ---------------------------------------------------
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200

_user32 = ctypes.windll.user32
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]


def make_click_through(hwnd: int) -> None:
    ex = _user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
    ex |= (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
           | WS_EX_NOACTIVATE)
    _user32.SetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE, ex)


def raise_topmost(hwnd: int) -> None:
    """Re-assert topmost without stealing focus (keeps us over a game)."""
    _user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_TOPMOST),
                         0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
                         | SWP_NOOWNERZORDER)


@dataclass
class OverlayStyle:
    segments: int = 12          # number of compass-bearing blocks around border
    tick_fraction: float = 1.0  # 1.0 = a loud block fills its slot (no gap)
    depth: int = 29             # FIXED inward thickness (never grows inward)
    min_halfw: float = 4.0      # half-length of a just-audible block (px)
    min_alpha: int = 120        # brightness of a just-audible block (0..255)
    gamma: float = 0.8          # <1 = sounds reach big size sooner (more growth)
    opacity: float = 0.85       # overall transparency (1 = solid, lower = see-through)
    brightness: float = 1.0     # scales block brightness ONLY (not its size)
    # Palette: bright magenta — rare in games, high contrast on any background.
    far_color: QtGui.QColor = None
    near_color: QtGui.QColor = None

    def __post_init__(self):
        if self.far_color is None:
            self.far_color = QtGui.QColor(255, 130, 240)   # soft pink-magenta
        if self.near_color is None:
            self.near_color = QtGui.QColor(255, 0, 220)    # bright magenta


def _perimeter_ticks(W, H, m):
    """Evenly spaced ticks walking the border clockwise from the top-left.

    Each tick: (x, y, nx, ny, tx, ty, angle) where (nx,ny) is the inward edge
    normal, (tx,ty) the along-edge tangent, and angle the compass bearing
    (0=top/front, clockwise) used to look up which sound lights it."""
    cx, cy = W / 2.0, H / 2.0
    perim = 2.0 * (W + H)
    step = perim / m
    ticks = []
    for i in range(m):
        # start block 0 at top-centre (12 o'clock), then evenly clockwise
        s = (W / 2.0 + i * step) % perim
        if s < W:                       # top edge, left -> right
            x, y, n, t = s, 0.0, (0.0, 1.0), (1.0, 0.0)
        elif s < W + H:                 # right edge, top -> bottom
            x, y, n, t = W, s - W, (-1.0, 0.0), (0.0, 1.0)
        elif s < 2 * W + H:             # bottom edge, right -> left
            x, y, n, t = W - (s - W - H), H, (0.0, -1.0), (-1.0, 0.0)
        else:                           # left edge, bottom -> top
            x, y, n, t = 0.0, H - (s - 2 * W - H), (1.0, 0.0), (0.0, -1.0)
        ang = math.degrees(math.atan2(x - cx, -(y - cy))) % 360.0
        ticks.append((x, y, n[0], n[1], t[0], t[1], ang))
    return ticks, step


class OverlayWindow(QtWidgets.QWidget):
    def __init__(self, style: OverlayStyle | None = None,
                 screen: QtGui.QScreen | None = None):
        super().__init__(None,
                         QtCore.Qt.WindowType.FramelessWindowHint
                         | QtCore.Qt.WindowType.WindowStaysOnTopHint
                         | QtCore.Qt.WindowType.Tool)
        self.style_ = style or OverlayStyle()
        self._intensity: dict[str, float] = {}
        self._lfe = 0.0
        self._ticks = []
        self._slot = 0.0

        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)

        scr = screen or QtWidgets.QApplication.primaryScreen()
        self.setGeometry(scr.geometry())
        self._rebuild_geometry()

    # -- public API -------------------------------------------------------
    def set_channel_intensities(self, values: dict[str, float],
                                lfe: float = 0.0) -> None:
        self._intensity = dict(values)
        self._lfe = max(0.0, min(1.0, lfe))
        self.update()

    def showEvent(self, e: QtGui.QShowEvent) -> None:
        super().showEvent(e)
        make_click_through(int(self.winId()))

    def keep_on_top(self) -> None:
        raise_topmost(int(self.winId()))

    def set_screen(self, scr: QtGui.QScreen) -> None:
        """Move the overlay to cover a different monitor."""
        self.setGeometry(scr.geometry())
        self._rebuild_geometry()
        make_click_through(int(self.winId()))
        self.update()

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e)
        self._rebuild_geometry()

    # -- geometry / direction model --------------------------------------
    def _rebuild_geometry(self):
        W, H = self.width(), self.height()
        self._ticks, self._slot = _perimeter_ticks(W, H, self.style_.segments)

    def _tick_intensities(self):
        """Place sound around the ring by interpolating the channel levels.

        Each block blends its TWO nearest channels (including silent ones), so a
        lone sound makes a small arc that fades to the neighbouring silent
        directions, and a sound panned between two channels lights the in-between
        blocks — using all the blocks, not just the 7 channel points. With only
        2 channels (stereo) it falls back to snapping to the nearest block.
        """
        out = [0.0] * len(self._ticks)
        chans = [(CHANNEL_ANGLES[l], min(1.0, v))
                 for l, v in self._intensity.items() if l in CHANNEL_ANGLES]
        if not chans:
            return out

        if len(chans) < 5:  # stereo: snap to nearest block
            for ang, v in chans:
                if v <= 0.003:
                    continue
                i_near = min(range(len(self._ticks)),
                             key=lambda i: min(abs(self._ticks[i][6] - ang),
                                               360.0 - abs(self._ticks[i][6] - ang)))
                out[i_near] = max(out[i_near], v)
            return out

        for i, tk in enumerate(self._ticks):
            th = tk[6]
            nearest = sorted((min(abs(a - th), 360.0 - abs(a - th)), e)
                             for a, e in chans)
            (d0, e0), (d1, e1) = nearest[0], nearest[1]
            tot = d0 + d1
            out[i] = e0 if tot < 1e-6 else (e0 * d1 + e1 * d0) / tot
        return out

    # -- painting ---------------------------------------------------------
    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(QtCore.Qt.PenStyle.NoPen)

        st = self.style_
        far, near = st.far_color, st.near_color
        vals = self._tick_intensities()
        slot_half = 0.5 * self._slot * st.tick_fraction  # max half-length
        depth = st.depth  # FIXED inward thickness for every block
        for tk, b in zip(self._ticks, vals):
            if b <= 0.01:          # silent block -> not drawn at all
                continue
            x, y, nx, ny, tx, ty, _ang = tk
            vv = b ** st.gamma
            # loudness -> LENGTH along the edge (Size). Brightness is a separate
            # multiplier so the two sliders don't both change apparent size.
            halfw = max(st.min_halfw, slot_half * vv)
            bv = min(1.0, vv * st.brightness)
            edge_alpha = int(st.min_alpha + (255 - st.min_alpha) * bv)
            col = QtGui.QColor(
                int(far.red() + (near.red() - far.red()) * bv),
                int(far.green() + (near.green() - far.green()) * bv),
                int(far.blue() + (near.blue() - far.blue()) * bv))
            c0 = QtGui.QColor(col); c0.setAlpha(int(edge_alpha * st.opacity))
            c1 = QtGui.QColor(col); c1.setAlpha(0)
            grad = QtGui.QLinearGradient(x, y, x + nx * depth, y + ny * depth)
            grad.setColorAt(0.0, c0)   # brightest at the screen edge
            grad.setColorAt(1.0, c1)   # fades toward the centre
            ax, ay = x - tx * halfw, y - ty * halfw
            bx, by = x + tx * halfw, y + ty * halfw
            poly = QtGui.QPolygonF([
                QtCore.QPointF(ax, ay), QtCore.QPointF(bx, by),
                QtCore.QPointF(bx + nx * depth, by + ny * depth),
                QtCore.QPointF(ax + nx * depth, ay + ny * depth)])
            p.setBrush(QtGui.QBrush(grad))
            p.drawPolygon(poly)
        p.end()
