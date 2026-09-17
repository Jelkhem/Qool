"""تسجيل الميكروفون بـ sounddevice إلى ذاكرة محدودة (بلا ملفات مؤقتة)."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

TARGET_RATE = 16000
_HOSTAPI_PREFERENCE = ("Windows WASAPI", "MME", "Windows DirectSound")


class AudioError(RuntimeError):
    pass


def _sd():
    import sounddevice as sd
    return sd


def list_input_devices() -> list[str]:
    """أسماء أجهزة الإدخال المتاحة (بدون تكرار بين واجهات Windows الصوتية)."""
    sd = _sd()
    try:
        sd._terminate(); sd._initialize()  # تحديث القائمة بعد توصيل/فصل جهاز
    except Exception:
        pass
    apis = sd.query_hostapis()
    names: list[str] = []
    for api_name in _HOSTAPI_PREFERENCE:
        for api in apis:
            if api["name"] != api_name:
                continue
            for idx in api["devices"]:
                d = sd.query_devices(idx)
                if d["max_input_channels"] > 0 and d["name"] not in names:
                    if api_name != "Windows WASAPI" and any(n.startswith(d["name"][:28]) for n in names):
                        continue
                    names.append(d["name"])
    return names


def _find_device(name: str) -> tuple[int | None, str]:
    """يرجع (فهرس الجهاز، اسم واجهة الصوت). None = الافتراضي."""
    sd = _sd()
    apis = sd.query_hostapis()
    if not name:
        for api_name in _HOSTAPI_PREFERENCE:
            for api in apis:
                if api["name"] == api_name and api["default_input_device"] >= 0:
                    return api["default_input_device"], api_name
        return None, ""
    for api_name in _HOSTAPI_PREFERENCE:
        for api in apis:
            if api["name"] != api_name:
                continue
            for idx in api["devices"]:
                d = sd.query_devices(idx)
                if d["max_input_channels"] > 0 and (d["name"] == name or name.startswith(d["name"]) or d["name"].startswith(name)):
                    return idx, api_name
    raise AudioError(f"الميكروفون «{name}» غير موجود. اختر جهازًا آخر من الإعدادات.")


def resample(audio: np.ndarray, src_rate: int, dst_rate: int = TARGET_RATE) -> np.ndarray:
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    # مرشح تمرير منخفض (windowed-sinc) لمنع التشويه ثم استيفاء خطي
    cutoff = 0.45 * min(src_rate, dst_rate) / src_rate
    taps = 101
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff * n) * np.hamming(taps)
    h /= h.sum()
    filtered = np.convolve(audio, h, mode="same") if src_rate > dst_rate else audio
    duration = audio.size / src_rate
    out_n = int(round(duration * dst_rate))
    x_old = np.arange(audio.size) / src_rate
    x_new = np.arange(out_n) / dst_rate
    return np.interp(x_new, x_old, filtered).astype(np.float32)


@dataclass
class AudioStats:
    duration: float
    peak: float
    rms_dbfs: float


def audio_stats(audio: np.ndarray, rate: int = TARGET_RATE) -> AudioStats:
    if audio.size == 0:
        return AudioStats(0.0, 0.0, -120.0)
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    return AudioStats(audio.size / rate, peak, 20 * np.log10(max(rms, 1e-6)))


class Recorder:
    """تسجيل عبر callback؛ مستوى الصوت يُقرأ من الخاصية level. الحد الأقصى يمنع نمو الذاكرة."""

    def __init__(self):
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._max_frames = 0
        self._rate = TARGET_RATE
        self._lock = threading.Lock()
        self.level = 0.0
        self.error: str | None = None
        self.limit_reached = False
        self.started_at = 0.0
        self.on_problem = None  # callable(str) من خيط الصوت

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at if self.is_recording else 0.0

    def start(self, device_name: str = "", max_seconds: float = 900) -> str:
        if self._stream is not None:
            raise AudioError("التسجيل يعمل بالفعل")
        sd = _sd()
        idx, api_name = _find_device(device_name)
        self._chunks, self._frames = [], 0
        self.level, self.error, self.limit_reached = 0.0, None, False
        attempts = []
        info = sd.query_devices(idx) if idx is not None else sd.query_devices(kind="input")
        native = int(info["default_samplerate"]) or 48000
        channels = 1 if info["max_input_channels"] >= 1 else 0
        if channels == 0:
            raise AudioError("الجهاز المختار لا يدعم التسجيل")
        for rate in (TARGET_RATE, native):
            extra = None
            if api_name == "Windows WASAPI" and rate != native:
                extra = sd.WasapiSettings(auto_convert=True)
            attempts.append((rate, extra))
        last_exc = None
        for rate, extra in attempts:
            try:
                self._rate = rate
                self._max_frames = int(max_seconds * rate)
                stream = sd.InputStream(device=idx, channels=1, samplerate=rate, dtype="float32",
                                        blocksize=int(rate * 0.05), callback=self._callback,
                                        finished_callback=self._finished, extra_settings=extra)
                stream.start()
                self._stream = stream
                self.started_at = time.monotonic()
                log.info("بدأ التسجيل: api=%s rate=%s", api_name, rate)
                return info["name"]
            except Exception as e:  # تجربة المعدل التالي
                last_exc = e
                log.warning("فشل فتح الميكروفون بمعدل %s: %s", rate, e)
        raise AudioError(f"تعذر فتح الميكروفون: {last_exc}")

    def _callback(self, indata, frames, time_info, status):
        if status and status.input_overflow:
            log.debug("input overflow")
        block = indata[:, 0].copy()
        self.level = float(min(1.0, np.sqrt(np.mean(block * block)) * 6))
        with self._lock:
            if self._frames + len(block) > self._max_frames:
                if not self.limit_reached:
                    self.limit_reached = True
                    if self.on_problem:
                        self.on_problem("limit")
                return
            self._chunks.append(block)
            self._frames += len(block)

    def _finished(self):
        # يُستدعى عند الإيقاف الطبيعي أو انقطاع الجهاز
        if self._stream is not None and not getattr(self, "_stopping", False):
            self.error = "انقطع الميكروفون أثناء التسجيل"
            if self.on_problem:
                self.on_problem("device_lost")

    def stop(self) -> np.ndarray:
        stream, self._stream = self._stream, None
        if stream is not None:
            self._stopping = True
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("خطأ عند إغلاق الميكروفون")
            finally:
                self._stopping = False
        with self._lock:
            chunks, self._chunks, self._frames = self._chunks, [], 0
        self.level = 0.0
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        return resample(audio, self._rate)

    def abort(self) -> None:
        self.stop()
