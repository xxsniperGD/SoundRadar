"""Map per-channel RMS to directional loudness for the compass overlay.

  * channels_to_directions: normalized 0..1 loudness per channel label.
  * DirectionEnvelopes: fast-attack / slow-decay smoothing so bars grow and
    fade smoothly instead of strobing (readability + no harsh flicker).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .audio import Levels


@dataclass
class AnalysisConfig:
    floor_db: float = -55.0   # below this = silence (sensitivity threshold)
    ceil_db: float = -8.0     # at/above this = full brightness
    gain: float = 1.0         # multiplier applied after normalization
    attack_ms: float = 25.0   # fast rise
    decay_ms: float = 280.0   # slow fade
    # directional contrast: subtract this fraction of the cross-channel average
    # so ambient sound (energy in ALL channels) is suppressed and only sounds
    # that stand out in a direction light up. 0 = off (show absolute level).
    contrast: float = 0.85


def _norm_db(rms: float, cfg: AnalysisConfig) -> float:
    db = 20.0 * math.log10(max(rms, 1e-6))
    v = (db - cfg.floor_db) / (cfg.ceil_db - cfg.floor_db)
    return max(0.0, min(1.0, v)) * cfg.gain


def channels_to_directions(levels: Levels,
                           cfg: AnalysisConfig) -> dict[str, float]:
    """Normalized 0..1 loudness per channel label (incl. LFE), pre-envelope.

    Each real channel keeps its own direction so the compass overlay can place
    it. With cfg.contrast > 0, the energy common to all channels (ambient) is
    subtracted, so only directionally-dominant sounds stay lit.
    """
    out: dict[str, float] = {}
    if levels.channels == 0 or levels.rms.size == 0:
        return out
    for label, rms in zip(levels.labels, levels.rms):
        out[label] = _norm_db(float(rms), cfg)

    if cfg.contrast > 0.0:
        dirs = [l for l in out if l != "LFE"]  # LFE is non-directional
        if dirs:
            base = (sum(out[l] for l in dirs) / len(dirs)) * cfg.contrast
            for l in dirs:
                out[l] = max(0.0, out[l] - base)
    return out


def _coef(dt_s: float, tau_ms: float) -> float:
    if tau_ms <= 0:
        return 1.0
    return 1.0 - math.exp(-dt_s / (tau_ms / 1000.0))


class AdaptiveBaseline:
    """Subtract a slow per-direction running average so a constantly-loud
    channel (front music/ambient) settles down and only NEW directional events
    (footsteps, gunshots, anything that changes) stand out. amount 0 = off."""

    def __init__(self, amount: float = 0.6, tau_ms: float = 1500.0):
        self.amount = amount
        self.tau_ms = tau_ms
        self._avg: dict[str, float] = {}

    def apply(self, levels: dict[str, float], dt_s: float) -> dict[str, float]:
        if self.amount <= 0.0:
            return levels
        coef = _coef(dt_s, self.tau_ms)
        out = {}
        for k, v in levels.items():
            a = self._avg.get(k, 0.0)
            a += (v - a) * coef
            self._avg[k] = a
            out[k] = max(0.0, v - self.amount * a)
        return out


class DirectionEnvelopes:
    """Fast-attack / slow-decay smoothing over an arbitrary label set."""

    def __init__(self, cfg: AnalysisConfig):
        self.cfg = cfg
        self._val: dict[str, float] = {}

    def update(self, target: dict[str, float], dt_s: float) -> dict[str, float]:
        a = _coef(dt_s, self.cfg.attack_ms)
        d = _coef(dt_s, self.cfg.decay_ms)
        for k in set(self._val) | set(target):
            cur = self._val.get(k, 0.0)
            tgt = target.get(k, 0.0)
            coef = a if tgt > cur else d
            self._val[k] = cur + (tgt - cur) * coef
        return dict(self._val)
