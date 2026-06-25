"""WASAPI loopback capture + per-channel RMS analysis.

Captures whatever the chosen output device is playing (loopback) in small
frames and exposes per-channel RMS energy. Designed to run on its own thread;
the newest frame's levels are kept in a thread-safe latest-value buffer so the
UI never blocks on a growing queue (we only care about the newest frame).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np
import soundcard as sc


# ---- channel layout -------------------------------------------------------
# Standard WASAPI 7.1 channel order (KSAUDIO / Windows):
#   0 Front Left   1 Front Right   2 Center   3 LFE
#   4 Rear Left    5 Rear Right    6 Side Left 7 Side Right
# We label by index so the monitor and overlay agree on naming.
LAYOUT_71 = ["FL", "FR", "C", "LFE", "RL", "RR", "SL", "SR"]
LAYOUT_51 = ["FL", "FR", "C", "LFE", "RL", "RR"]
LAYOUT_STEREO = ["L", "R"]


def labels_for(channels: int) -> list[str]:
    if channels >= 8:
        return LAYOUT_71 + [f"ch{i}" for i in range(8, channels)]
    if channels == 6:
        return LAYOUT_51
    if channels == 2:
        return LAYOUT_STEREO
    return [f"ch{i}" for i in range(channels)]


@dataclass
class CaptureConfig:
    samplerate: int = 48000
    # ~12 ms frames at 48 kHz for low latency.
    blocksize: int = 576
    device_name: str | None = None  # None -> default speaker's loopback
    channels: int | None = None     # None -> device native channel count


@dataclass
class Levels:
    """Latest per-channel RMS snapshot. Thread-safe via the holder's lock."""
    rms: np.ndarray = field(default_factory=lambda: np.zeros(0))
    channels: int = 0
    labels: list[str] = field(default_factory=list)
    ts: float = 0.0


def list_loopback_devices() -> list:
    return [m for m in sc.all_microphones(include_loopback=True)
            if getattr(m, "isloopback", False)]


def list_output_devices() -> list:
    """Physical/virtual playback devices — for choosing where the mono mix
    plays in surround mode (e.g. your headset)."""
    return list(sc.all_speakers())


def _resolve_mic(cfg: CaptureConfig):
    if cfg.device_name is None:
        spk = sc.default_speaker()
        return sc.get_microphone(id=str(spk.name), include_loopback=True), spk
    mic = sc.get_microphone(id=cfg.device_name, include_loopback=True)
    return mic, mic


class LoopbackCapture:
    """Background loopback capture publishing the newest per-channel RMS."""

    def __init__(self, cfg: CaptureConfig | None = None):
        self.cfg = cfg or CaptureConfig()
        self._lock = threading.Lock()
        self._levels = Levels()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.actual_channels: int | None = None

    def get_levels(self) -> Levels:
        with self._lock:
            return Levels(self._levels.rms.copy(), self._levels.channels,
                          self._levels.labels, self._levels.ts)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="loopback",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        mic, _ = _resolve_mic(self.cfg)
        channels = self.cfg.channels or mic.channels
        with mic.recorder(samplerate=self.cfg.samplerate,
                          channels=channels,
                          blocksize=self.cfg.blocksize) as rec:
            while not self._stop.is_set():
                data = rec.record(numframes=self.cfg.blocksize)  # (frames, ch)
                if data.size == 0:
                    continue
                self.actual_channels = data.shape[1]
                rms = np.sqrt(np.mean(np.square(data, dtype=np.float64),
                                      axis=0))
                with self._lock:
                    self._levels = Levels(
                        rms=rms.astype(np.float32),
                        channels=data.shape[1],
                        labels=labels_for(data.shape[1]),
                        ts=time.perf_counter(),
                    )


def rms_to_dbfs(rms: np.ndarray) -> np.ndarray:
    """Convert linear RMS to dBFS (-inf..0). Floor at -120 dB for display."""
    return 20.0 * np.log10(np.maximum(rms, 1e-6))
