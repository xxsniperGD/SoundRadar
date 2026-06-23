"""Per-application audio capture via the Windows Process Loopback API.

Captures the audio rendered by a specific process (and its child processes)
*before* the global endpoint mix — so it is not affected by the Windows
"Mono audio" accessibility downmix. This lets us read the game's real
left/right stream while the user keeps full mono hearing, with zero changes to
their audio output path.

Requires Windows 10 build 20348+ / Windows 11. Uses ActivateAudioInterfaceAsync
with AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK.

Reference: Microsoft "ApplicationLoopback" sample.
"""

from __future__ import annotations

import collections
import ctypes
import os
import sys
import threading
import time
from ctypes import POINTER, byref, c_void_p, wintypes

import numpy as np
import soundcard as sc

# comtypes CoInitializes the main thread at import. soundcard / Qt already put
# the process in a multithreaded apartment, so force comtypes to match (MTA)
# instead of its default STA, which would fail with RPC_E_CHANGED_MODE.
sys.coinit_flags = 0  # COINIT_MULTITHREADED
import comtypes  # noqa: E402
from comtypes import GUID, IUnknown, COMMETHOD, COMObject  # noqa: E402

from .audio import Levels, labels_for
from .router import downmix_to_mono

# --- constants -------------------------------------------------------------
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_IEEE_FLOAT = 0x0003
VT_BLOB = 65
S_OK = 0
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0

AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1

VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = \
    "VAD\\Process_Loopback"

REFERENCE_TIME = ctypes.c_longlong

_kernel32 = ctypes.windll.kernel32
_mmdevapi = ctypes.windll.mmdevapi


# --- structures ------------------------------------------------------------
class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEFORMATEXTENSIBLE(ctypes.Structure):
    _fields_ = [
        ("Format", WAVEFORMATEX),
        ("wValidBitsPerSample", wintypes.WORD),
        ("dwChannelMask", wintypes.DWORD),
        ("SubFormat", GUID),
    ]


WAVE_FORMAT_EXTENSIBLE = 0xFFFE
KSDATAFORMAT_SUBTYPE_IEEE_FLOAT = GUID("{00000003-0000-0010-8000-00AA00389B71}")
# channel masks: stereo, 5.1, 7.1
_CHANNEL_MASKS = {2: 0x3, 6: 0x3F, 8: 0x63F}


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    _fields_ = [
        ("TargetProcessId", wintypes.DWORD),
        ("ProcessLoopbackMode", ctypes.c_int),
    ]


class _ACT_U(ctypes.Union):
    _fields_ = [("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS)]


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("ActivationType", ctypes.c_int), ("u", _ACT_U)]


class PROPVARIANT(ctypes.Structure):
    # Only the BLOB member is needed. Padding makes the pointer 8-aligned (x64).
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("cbSize", ctypes.c_ulong),
        ("_pad", ctypes.c_ulong),
        ("pBlobData", c_void_p),
    ]


# --- COM interfaces --------------------------------------------------------
class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "GetBuffer",
                  (['out'], POINTER(POINTER(ctypes.c_byte)), "ppData"),
                  (['out'], POINTER(wintypes.UINT), "pNumFramesToRead"),
                  (['out'], POINTER(wintypes.DWORD), "pdwFlags"),
                  (['out'], POINTER(ctypes.c_ulonglong), "pu64DevicePosition"),
                  (['out'], POINTER(ctypes.c_ulonglong), "pu64QPCPosition")),
        COMMETHOD([], comtypes.HRESULT, "ReleaseBuffer",
                  (['in'], wintypes.UINT, "NumFramesRead")),
        COMMETHOD([], comtypes.HRESULT, "GetNextPacketSize",
                  (['out'], POINTER(wintypes.UINT), "pNumFramesInNextPacket")),
    ]


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "Initialize",
                  (['in'], ctypes.c_uint, "ShareMode"),
                  (['in'], wintypes.DWORD, "StreamFlags"),
                  (['in'], REFERENCE_TIME, "hnsBufferDuration"),
                  (['in'], REFERENCE_TIME, "hnsPeriodicity"),
                  (['in'], POINTER(WAVEFORMATEX), "pFormat"),
                  (['in'], POINTER(GUID), "AudioSessionGuid")),
        COMMETHOD([], comtypes.HRESULT, "GetBufferSize",
                  (['out'], POINTER(wintypes.UINT), "pNumBufferFrames")),
        COMMETHOD([], comtypes.HRESULT, "GetStreamLatency",
                  (['out'], POINTER(REFERENCE_TIME), "phnsLatency")),
        COMMETHOD([], comtypes.HRESULT, "GetCurrentPadding",
                  (['out'], POINTER(wintypes.UINT), "pNumPaddingFrames")),
        COMMETHOD([], comtypes.HRESULT, "IsFormatSupported",
                  (['in'], ctypes.c_uint, "ShareMode"),
                  (['in'], POINTER(WAVEFORMATEX), "pFormat"),
                  (['out'], POINTER(POINTER(WAVEFORMATEX)), "ppClosestMatch")),
        COMMETHOD([], comtypes.HRESULT, "GetMixFormat",
                  (['out'], POINTER(POINTER(WAVEFORMATEX)), "ppDeviceFormat")),
        COMMETHOD([], comtypes.HRESULT, "GetDevicePeriod",
                  (['out'], POINTER(REFERENCE_TIME), "phnsDefaultDevicePeriod"),
                  (['out'], POINTER(REFERENCE_TIME), "phnsMinimumDevicePeriod")),
        COMMETHOD([], comtypes.HRESULT, "Start"),
        COMMETHOD([], comtypes.HRESULT, "Stop"),
        COMMETHOD([], comtypes.HRESULT, "Reset"),
        COMMETHOD([], comtypes.HRESULT, "SetEventHandle",
                  (['in'], wintypes.HANDLE, "eventHandle")),
        COMMETHOD([], comtypes.HRESULT, "GetService",
                  (['in'], POINTER(GUID), "riid"),
                  (['out'], POINTER(POINTER(IAudioCaptureClient)), "ppv")),
    ]


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B84}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "GetActivateResult",
                  (['out'], POINTER(comtypes.HRESULT), "activateResult"),
                  (['out'], POINTER(POINTER(IUnknown)), "activatedInterface")),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = [
        COMMETHOD([], comtypes.HRESULT, "ActivateCompleted",
                  (['in'], POINTER(IActivateAudioInterfaceAsyncOperation),
                   "activateOperation")),
    ]


class IAgileObject(IUnknown):
    # Marker interface (no methods): declares the object free-threaded so COM
    # will not marshal it and the async callback can fire on a pool thread.
    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_ = []


class _CompletionHandler(COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self):
        super().__init__()
        self.done = threading.Event()

    def ActivateCompleted(self, *args):
        self.done.set()
        return S_OK


_ActivateAudioInterfaceAsync = _mmdevapi.ActivateAudioInterfaceAsync
_ActivateAudioInterfaceAsync.restype = comtypes.HRESULT
_ActivateAudioInterfaceAsync.argtypes = [
    wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT),
    POINTER(IActivateAudioInterfaceCompletionHandler),
    POINTER(POINTER(IActivateAudioInterfaceAsyncOperation)),
]


def _make_format(samplerate=48000, channels=2):
    """Float32 capture format. Stereo can use plain WAVEFORMATEX, but >2
    channels MUST be WAVEFORMATEXTENSIBLE with a channel mask or WASAPI
    rejects it with E_INVALIDARG."""
    block = channels * 4
    if channels <= 2:
        fmt = WAVEFORMATEX()
        fmt.wFormatTag = WAVE_FORMAT_IEEE_FLOAT
        fmt.nChannels = channels
        fmt.nSamplesPerSec = samplerate
        fmt.wBitsPerSample = 32
        fmt.nBlockAlign = block
        fmt.nAvgBytesPerSec = samplerate * block
        fmt.cbSize = 0
        return fmt
    ext = WAVEFORMATEXTENSIBLE()
    ext.Format.wFormatTag = WAVE_FORMAT_EXTENSIBLE
    ext.Format.nChannels = channels
    ext.Format.nSamplesPerSec = samplerate
    ext.Format.wBitsPerSample = 32
    ext.Format.nBlockAlign = block
    ext.Format.nAvgBytesPerSec = samplerate * block
    ext.Format.cbSize = 22
    ext.wValidBitsPerSample = 32
    ext.dwChannelMask = _CHANNEL_MASKS.get(channels, (1 << channels) - 1)
    ext.SubFormat = KSDATAFORMAT_SUBTYPE_IEEE_FLOAT
    return ext


def find_process_pids(name: str) -> list[int]:
    """Return PIDs whose executable name contains `name` (case-insensitive)."""
    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    pids = []
    needle = name.lower()
    if _kernel32.Process32FirstW(snap, byref(entry)):
        while True:
            if needle in entry.szExeFile.lower():
                pids.append(entry.th32ProcessID)
            if not _kernel32.Process32NextW(snap, byref(entry)):
                break
    _kernel32.CloseHandle(snap)
    return pids


class ProcessLoopbackCapture:
    """Capture a process tree's audio (pre-mono). Publishes per-channel RMS.

    include=True  -> capture only the target process tree (the game).
    include=False -> capture everything EXCEPT the target tree (pass your own
                     PID to capture all system audio but not SoundRadar).
    """

    def __init__(self, pid: int, samplerate: int = 48000, channels: int = 2,
                 include: bool = True, play_mono: bool = False,
                 output_name: str = "Headphones", out_gain: float = 0.5):
        self.pid = pid
        self.include = include
        self.samplerate = samplerate
        self.channels = channels
        # optional: downmix the captured audio to mono and play it to the
        # headphones, so the user hears everything while the game renders to a
        # silent surround device.
        self.play_mono = play_mono
        self.output_name = output_name
        self.out_gain = out_gain
        self._lock = threading.Lock()
        self._levels = Levels()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._play_thread: threading.Thread | None = None
        self._buf = collections.deque()
        self._buf_samples = 0
        self._buf_lock = threading.Lock()
        self.error: str | None = None
        self.frames_seen = 0
        self.reconnects = 0
        self.peak_out = 0.0
        self.underruns = 0

    def get_levels(self) -> Levels:
        with self._lock:
            return Levels(self._levels.rms.copy(), self._levels.channels,
                          self._levels.labels, self._levels.ts)

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="procloop",
                                        daemon=True)
        self._thread.start()
        if self.play_mono:
            self._play_thread = threading.Thread(target=self._playback,
                                                 name="procplay", daemon=True)
            self._play_thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._play_thread:
            self._play_thread.join(timeout=2.0)

    # -- mono playback (capture/playback decoupled via a small ring buffer) --
    def _pull(self, n: int) -> np.ndarray:
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

    def _playback(self):
        n = 480
        target = int(0.04 * self.samplerate)  # 40 ms cushion
        out = sc.get_speaker(self.output_name)
        while not self._stop.is_set():
            with self._buf_lock:
                ready = self._buf_samples
            if ready >= target:
                break
            self._stop.wait(0.002)
        with out.player(samplerate=self.samplerate, channels=2,
                        blocksize=n) as player:
            while not self._stop.is_set():
                mono = self._pull(n)
                self.peak_out = float(np.max(np.abs(mono))) if mono.size else 0
                player.play(np.stack([mono, mono], axis=1))

    def _activate_client(self) -> IAudioClient:
        params = AUDIOCLIENT_ACTIVATION_PARAMS()
        params.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
        params.ProcessLoopbackParams.TargetProcessId = self.pid
        params.ProcessLoopbackParams.ProcessLoopbackMode = (
            PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE if self.include
            else PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE)
        self._params_keepalive = params  # must outlive the async call

        pv = PROPVARIANT()
        pv.vt = VT_BLOB
        pv.cbSize = ctypes.sizeof(params)
        pv.pBlobData = ctypes.cast(byref(params), c_void_p)

        handler = _CompletionHandler()
        handler_ptr = handler.QueryInterface(
            IActivateAudioInterfaceCompletionHandler)
        op = POINTER(IActivateAudioInterfaceAsyncOperation)()
        hr = _ActivateAudioInterfaceAsync(
            VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
            byref(IAudioClient._iid_), byref(pv), handler_ptr, byref(op))
        if hr != S_OK:
            raise OSError(f"ActivateAudioInterfaceAsync failed: {hr:#010x}")
        if not handler.done.wait(timeout=3.0):
            raise TimeoutError("activation did not complete")
        activate_result, iface = op.GetActivateResult()
        if activate_result != S_OK:
            raise OSError(f"activation result: {activate_result & 0xffffffff:#010x}")
        return iface.QueryInterface(IAudioClient)

    def _run(self):
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            # Retry loop: a sound-settings change / device reset invalidates the
            # capture stream; tear down and re-activate instead of going dark.
            while not self._stop.is_set():
                try:
                    self._session()
                except Exception as e:  # noqa: BLE001
                    self.error = f"{type(e).__name__}: {e}"
                    self.reconnects += 1
                    self._stop.wait(0.5)  # brief backoff, then re-activate
        finally:
            comtypes.CoUninitialize()

    def _session(self):
        """One capture session: activate, stream until stop or error."""
        client = self._activate_client()
        fmt = _make_format(self.samplerate, self.channels)
        if isinstance(fmt, WAVEFORMATEXTENSIBLE):
            wfx_ptr = ctypes.cast(byref(fmt), POINTER(WAVEFORMATEX))
            bpf = fmt.Format.nBlockAlign
        else:
            wfx_ptr = ctypes.cast(byref(fmt), POINTER(WAVEFORMATEX))
            bpf = fmt.nBlockAlign
        client.Initialize(
            AUDCLNT_SHAREMODE_SHARED,
            AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK,
            200000, 0, wfx_ptr, None)
        event = _kernel32.CreateEventW(None, False, False, None)
        client.SetEventHandle(event)
        capture = client.GetService(byref(IAudioCaptureClient._iid_))
        client.Start()
        ch = self.channels
        try:
            while not self._stop.is_set():
                if _kernel32.WaitForSingleObject(event, 200) != WAIT_OBJECT_0:
                    continue
                packet = capture.GetNextPacketSize()
                while packet:
                    pdata, nframes, flags, _dp, _qp = capture.GetBuffer()
                    if nframes:
                        if flags & AUDCLNT_BUFFERFLAGS_SILENT:
                            block = np.zeros((nframes, ch), dtype=np.float32)
                        else:
                            buf = ctypes.string_at(pdata, nframes * bpf)
                            block = np.frombuffer(
                                buf, dtype=np.float32).reshape(-1, ch)
                        self.frames_seen += nframes
                        rms = np.sqrt(np.mean(
                            np.square(block, dtype=np.float64),
                            axis=0)).astype(np.float32)
                        with self._lock:
                            self._levels = Levels(
                                rms, ch, labels_for(ch), time.perf_counter())
                        if self.play_mono:
                            mono = downmix_to_mono(
                                block, labels_for(ch)) * self.out_gain
                            np.clip(mono, -1.0, 1.0, out=mono)
                            with self._buf_lock:
                                self._buf.append(mono)
                                self._buf_samples += mono.shape[0]
                                cap_n = int(0.12 * self.samplerate)
                                while self._buf_samples > cap_n and self._buf:
                                    old = self._buf.popleft()
                                    self._buf_samples -= old.shape[0]
                    capture.ReleaseBuffer(nframes)
                    packet = capture.GetNextPacketSize()
        finally:
            try:
                client.Stop()
            finally:
                _kernel32.CloseHandle(event)
