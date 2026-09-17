"""إدارة نموذج faster-whisper: تحميل على GPU مع fallback واضح للـCPU، وتفريغ محلي."""
from __future__ import annotations

import gc
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .cleanup import Segment

log = logging.getLogger(__name__)

# لا اتصال بالشبكة ولا telemetry أثناء الإملاء
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

_dll_setup_done = False


def setup_cuda_dll_paths() -> list[str]:
    """يضيف مكتبات cuBLAS/cuDNN المثبتة داخل البيئة الافتراضية فقط (لا تغيير على مستوى النظام)."""
    global _dll_setup_done
    added = []
    for base in sys.path:
        nv = Path(base) / "nvidia"
        if not nv.is_dir():
            continue
        for sub in ("cublas", "cudnn", "cuda_nvrtc"):
            d = nv / sub / "bin"
            if d.is_dir():
                if not _dll_setup_done:
                    os.add_dll_directory(str(d))
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
                added.append(str(d))
    _dll_setup_done = True
    return added


def gpu_memory_mb() -> tuple[int, int] | None:
    """(المستخدم، المتاح) لكارت الشاشة كله بالميجابايت، أو None."""
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            m = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0))
            return m.used // 2**20, m.free // 2**20
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None


class TranscriptionCancelled(Exception):
    pass


@dataclass
class LoadInfo:
    device: str
    compute_type: str
    load_seconds: float
    model_name: str
    fallback_reason: str = ""


@dataclass
class TranscriptResult:
    segments: list[Segment]
    duration: float
    speech_duration: float
    process_seconds: float
    device: str
    fallback_reason: str = ""
    dropped_segments: int = 0


def _is_oom(e: Exception) -> bool:
    s = str(e).lower()
    return "out of memory" in s or "cublas_status_alloc_failed" in s or "alloc" in s and "cuda" in s


class Transcriber:
    """كل الاستدعاءات يجب أن تتم من خيط العامل نفسه."""

    def __init__(self):
        self.model = None
        self.info: LoadInfo | None = None
        self._key = None
        self.last_used = 0.0

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def unload(self) -> None:
        if self.model is not None:
            log.info("تحرير النموذج من الذاكرة")
        self.model = None
        self.info = None
        self._key = None
        gc.collect()

    def ensure_loaded(self, model_dir: str, model_name: str, device_pref: str, gpu_ct: str, cpu_ct: str,
                      vram_needed_mb: int = 1500) -> LoadInfo:
        key = (model_dir, device_pref, gpu_ct, cpu_ct)
        if self.model is not None and self._key == key:
            return self.info
        self.unload()
        from faster_whisper import WhisperModel

        reason = ""
        want_gpu = device_pref in ("auto", "cuda")
        if want_gpu:
            setup_cuda_dll_paths()
            try:
                import ctranslate2

                n = ctranslate2.get_cuda_device_count()
            except Exception as e:
                n, reason = 0, f"تعذر فحص CUDA: {e}"
            if n == 0:
                reason = reason or "لا يوجد كارت NVIDIA/CUDA متاح"
            else:
                mem = gpu_memory_mb()
                if mem and mem[1] < vram_needed_mb:
                    reason = f"ذاكرة كارت الشاشة المتاحة {mem[1]} MB أقل من المطلوب (~{vram_needed_mb} MB)"
                else:
                    t = time.perf_counter()
                    try:
                        self.model = WhisperModel(model_dir, device="cuda", compute_type=gpu_ct)
                        self.info = LoadInfo("cuda", gpu_ct, time.perf_counter() - t, model_name)
                    except Exception as e:
                        log.warning("فشل التحميل على GPU: %s", e)
                        self.model = None
                        gc.collect()
                        reason = "نفدت ذاكرة كارت الشاشة" if _is_oom(e) else f"فشل تشغيل CUDA: {str(e)[:160]}"
        if self.model is None:
            t = time.perf_counter()
            threads = max(4, min(8, (os.cpu_count() or 8) // 2))
            self.model = WhisperModel(model_dir, device="cpu", compute_type=cpu_ct, cpu_threads=threads)
            self.info = LoadInfo("cpu", cpu_ct, time.perf_counter() - t, model_name,
                                 reason if want_gpu else "")
        self._key = key
        self.last_used = time.monotonic()
        log.info("تم تحميل النموذج %s على %s (%s) في %.2f ث %s", model_name, self.info.device,
                 self.info.compute_type, self.info.load_seconds, self.info.fallback_reason)
        return self.info

    def _run(self, audio, language, initial_prompt, hotwords, beam_size, cancel_check):
        segs, info = self.model.transcribe(
            audio,
            language=language,
            task="transcribe",
            beam_size=beam_size,
            initial_prompt=initial_prompt or None,
            hotwords=hotwords or None,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters=dict(threshold=0.35, min_silence_duration_ms=1000, speech_pad_ms=400),
            no_speech_threshold=0.6,
        )
        out, dropped = [], 0
        for s in segs:  # مولِّد: نتحقق من الإلغاء بين المقاطع
            if cancel_check():
                raise TranscriptionCancelled()
            if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
                dropped += 1
                continue
            out.append(Segment(s.text, s.start, s.end))
        return out, info, dropped

    def transcribe(self, audio: np.ndarray, *, language: str = "ar", initial_prompt: str = "",
                   hotwords: str = "", beam_size: int = 5,
                   cancel_check: Callable[[], bool] = lambda: False,
                   reload_cpu: Callable[[], LoadInfo] | None = None) -> TranscriptResult:
        if self.model is None:
            raise RuntimeError("النموذج غير محمَّل")
        t = time.perf_counter()
        fallback = ""
        try:
            segs, info, dropped = self._run(audio, language, initial_prompt, hotwords, beam_size, cancel_check)
        except TranscriptionCancelled:
            raise
        except Exception as e:
            if self.info and self.info.device == "cuda" and reload_cpu is not None:
                log.warning("فشل التفريغ على GPU، إعادة المحاولة على CPU: %s", e)
                fallback = "نفدت ذاكرة كارت الشاشة أثناء التفريغ؛ تم التحويل إلى CPU" if _is_oom(e) \
                    else f"خطأ في GPU أثناء التفريغ؛ تم التحويل إلى CPU ({str(e)[:120]})"
                self.unload()
                reload_cpu()
                t = time.perf_counter()
                segs, info, dropped = self._run(audio, language, initial_prompt, hotwords, beam_size, cancel_check)
            else:
                raise
        self.last_used = time.monotonic()
        return TranscriptResult(segs, info.duration, info.duration_after_vad or 0.0,
                                time.perf_counter() - t, self.info.device if self.info else "?", fallback, dropped)
