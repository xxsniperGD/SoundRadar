"""Mono router — capture 7.1, feed the radar, AND play a full mono mix.

Solves the hearing problem without VoiceMeeter's lossy downmix: SoundRadar sums
ALL captured channels into one complete mono signal and plays it to the
headphones itself, so nothing is dropped.

Capture and playback run on SEPARATE threads with a small ring buffer between
them. This is essential: a single record->play loop cannot keep real time and
the output starves (audio plays back slow / glitchy). The capture thread reads
continuously (so the loopback never overflows) and the playback thread drains
the buffer at the output device's own pace.

Signal flow:
    game -> VAIO3 (7.1) --loopback--> [capture thread]
                                         |-- per-channel RMS -> radar overlay
                                         '-- sum -> mono -> ring buffer
                                                              |
                                          [playback thread] --'-> headphones
"""

from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass

import numpy as np
import soundcard as sc

from .audio import Levels, labels_for


# Mono downmix weights by channel label. Everything is included so no sound is
# ever missed; center/surround at -3 dB (0.707), LFE kept substantial so
# low-end action stays audible.
DOWNMIX_WEIGHTS = {
    "FL": 1.0, "FR": 1.0, "L": 1.0, "R": 1.0,
    "C": 0.707,
    "LFE": 0.7,
    "RL": 0.707, "RR": 0.707,
    "SL": 0.707, "SR": 0.707,
}


def downmix_to_mono(frame: np.ndarray, labels: list[str]) -> np.ndarray:
    w = np.array([DOWNMIX_WEIGHTS.get(lbl, 1.0) for lbl in labels],
                 dtype=np.float32)
    return frame.astype(np.float32) @ w


@dataclass
class RouterConfig:
    samplerate: int = 48000
    blocksize: int = 480            # 10 ms
    source_name: str | None = None  # None -> default speaker loopback (VAIO3)
    output_name: str = "Headphones"
    out_gain: float = 0.5
    target_buffer_ms: float = 40.0  # latency cushion before playback starts
    max_buffer_ms: float = 120.0    # drop oldest beyond this (drift guard)


class MonoRouter:
    def __init__(self, cfg: RouterConfig | None = None):
        self.cfg = cfg or RouterConfig()
        self._lock = threading.Lock()
        self._levels = Levels()
        self._buf = collections.deque()  # mono float32 chunks
        self._buf_samples = 0
        self._buf_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.peak_out = 0.0
        self.underruns = 0
        self.drops = 0

    # -- radar interface (drop-in for LoopbackCapture) -------------------
    def get_levels(self) -> Levels:
        with self._lock:
            return Levels(self._levels.rms.copy(), self._levels.channels,
                          self._levels.labels, self._levels.ts)

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._capture, name="cap", daemon=True),
            threading.Thread(target=self._playback, name="play", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1.5)
        self._threads = []

    # -- threads ---------------------------------------------------------
    def _capture(self) -> None:
        if self.cfg.source_name is None:
            src = sc.get_microphone(id=str(sc.default_speaker().name),
                                    include_loopback=True)
        else:
            src = sc.get_microphone(id=self.cfg.source_name,
                                    include_loopback=True)
        n = self.cfg.blocksize
        max_samples = int(self.cfg.max_buffer_ms / 1000 * self.cfg.samplerate)
        with src.recorder(samplerate=self.cfg.samplerate, channels=None,
                          blocksize=n) as rec:
            while not self._stop.is_set():
                data = rec.record(numframes=n)
                if data.size == 0:
                    continue
                ch = data.shape[1]
                labels = labels_for(ch)
                rms = np.sqrt(np.mean(np.square(data, dtype=np.float64),
                                      axis=0)).astype(np.float32)
                with self._lock:
                    self._levels = Levels(rms, ch, labels, time.perf_counter())
                mono = downmix_to_mono(data, labels) * self.cfg.out_gain
                np.clip(mono, -1.0, 1.0, out=mono)
                with self._buf_lock:
                    self._buf.append(mono)
                    self._buf_samples += mono.shape[0]
                    # drift guard: drop oldest if we're running too far ahead
                    while self._buf_samples > max_samples and self._buf:
                        old = self._buf.popleft()
                        self._buf_samples -= old.shape[0]
                        self.drops += 1

    def _pull(self, n: int) -> np.ndarray:
        """Pull exactly n mono samples; pad with zeros on underrun."""
        out = np.zeros(n, dtype=np.float32)
        got = 0
        with self._buf_lock:
            while got < n and self._buf:
                chunk = self._buf[0]
                take = min(n - got, chunk.shape[0])
                out[got:got + take] = chunk[:take]
                if take == chunk.shape[0]:
                    self._buf.popleft()
                else:
                    self._buf[0] = chunk[take:]
                self._buf_samples -= take
                got += take
        if got < n:
            self.underruns += 1
        return out

    def _playback(self) -> None:
        out = sc.get_speaker(self.cfg.output_name)
        n = self.cfg.blocksize
        target = int(self.cfg.target_buffer_ms / 1000 * self.cfg.samplerate)
        # prime: wait until the cushion is filled so we never start starved
        while not self._stop.is_set():
            with self._buf_lock:
                ready = self._buf_samples
            if ready >= target:
                break
            time.sleep(0.002)
        with out.player(samplerate=self.cfg.samplerate, channels=2,
                        blocksize=n) as player:
            while not self._stop.is_set():
                mono = self._pull(n)
                self.peak_out = float(np.max(np.abs(mono))) if mono.size else 0.0
                player.play(np.stack([mono, mono], axis=1))
