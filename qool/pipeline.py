"""يربط النموذج بالتنظيف: صوت -> نص خام -> نص منظَّف."""
from __future__ import annotations

import logging

from .cleanup import clean_text, is_meaningless, join_segments
from .config import Settings
from .controller import NoSpeech, ProcessOutput
from .models import CATALOG, is_downloaded, is_valid_model_dir, model_path
from .transcriber import LoadInfo, Transcriber

log = logging.getLogger(__name__)


class ModelMissing(RuntimeError):
    pass


def resolve_model(settings: Settings) -> tuple[str, str, int]:
    """(مسار النموذج، اسمه، تقدير VRAM)."""
    if settings.custom_model_path:
        if not is_valid_model_dir(settings.custom_model_path):
            raise ModelMissing("مسار النموذج المخصص غير صالح (يجب أن يحتوي model.bin وconfig.json وtokenizer.json).")
        return settings.custom_model_path, "custom", 2600
    info = CATALOG.get(settings.model) or CATALOG["large-v3-turbo"]
    if not is_downloaded(info.key):
        raise ModelMissing(f"النموذج {info.key} غير منزَّل بعد (~{info.size_mb} MB). نزّله من الإعدادات.")
    need = info.vram_mb_int8_fp16 * (2 if settings.gpu_compute_type in ("float16", "bfloat16") else 1)
    return str(model_path(info.key)), info.key, need


class Pipeline:
    def __init__(self, settings: Settings, transcriber: Transcriber | None = None, on_load=None):
        self.settings = settings
        self.transcriber = transcriber or Transcriber()
        self.on_load = on_load  # callable(LoadInfo) لإبلاغ الواجهة بالجهاز/fallback
        self._groq_notice = ""  # سبب الرجوع من Groq للجهاز، يُعرض مع نتيجة التفريغ

    def load(self, force_cpu: bool = False) -> LoadInfo:
        s = self.settings
        path, name, need = resolve_model(s)
        already = self.transcriber.loaded
        info = self.transcriber.ensure_loaded(path, name, "cpu" if force_cpu else s.device,
                                              s.gpu_compute_type, s.cpu_compute_type, need)
        if self.on_load and (not already or force_cpu):
            self.on_load(info)
        return info

    def uses_groq(self) -> bool:
        return self.settings.stt_backend == "groq" and bool(self.settings.groq_key())

    def _groq_audio(self, audio, cancel_check) -> ProcessOutput | None:
        """يفرّغ على سيرفر Groq. يعيد None لو فشل والرجوع للجهاز مسموح."""
        from . import groq_stt

        s = self.settings
        try:
            # Groq ما عندهاش hotwords زي faster-whisper، فبنضم المصطلحات للسياق
            prompt = " ".join(x for x in (s.initial_prompt, s.vocabulary) if x.strip())
            segments, took = groq_stt.transcribe(audio, api_key=s.groq_key(), model=s.groq_model,
                                                 prompt=prompt)
        except groq_stt.GroqError as e:
            if not s.groq_fallback_local:
                raise
            log.warning("فشل التفريغ على Groq، الرجوع للجهاز: %s", e)
            self._groq_notice = f"تعذّر التفريغ أونلاين ({e}) — تم التفريغ على الجهاز."
            return None
        if cancel_check():
            from .transcriber import TranscriptionCancelled
            raise TranscriptionCancelled()
        raw = join_segments(segments, s.paragraph_gap_seconds)
        if is_meaningless(raw):
            raise NoSpeech("لم يُكتشف كلام واضح في التسجيل؛ لم يُكتب أي نص.")
        cleaned = clean_text(raw) if s.cleanup_enabled else raw.strip()
        if is_meaningless(cleaned):
            raise NoSpeech("لم يُكتشف كلام مفهوم في التسجيل؛ لم يُكتب أي نص.")
        seconds = len(audio) / 16000
        meta = dict(audio_seconds=round(seconds, 2), speech_seconds=round(seconds, 2),
                    process_seconds=round(took, 2), device="groq", model=s.groq_model,
                    fallback="", dropped_segments=0)
        log.info("تفريغ أونلاين: صوت=%.1fث معالجة=%.2fث موديل=%s", seconds, took, s.groq_model)
        return ProcessOutput(raw, cleaned, meta)

    def process_audio(self, audio, cancel_check=lambda: False) -> ProcessOutput:
        s = self.settings
        self._groq_notice = ""
        if self.uses_groq():
            out = self._groq_audio(audio, cancel_check)
            if out is not None:
                return out
        info = self.load()
        was_loaded_now = info.load_seconds if self.transcriber.last_used == 0 else 0.0
        res = self.transcriber.transcribe(
            audio, language="ar", initial_prompt=s.initial_prompt, hotwords=s.vocabulary,
            beam_size=s.beam_size, cancel_check=cancel_check,
            reload_cpu=lambda: self.load(force_cpu=True),
        )
        raw = join_segments(res.segments, s.paragraph_gap_seconds)
        if res.speech_duration <= 0.05 or is_meaningless(raw):
            raise NoSpeech("لم يُكتشف كلام واضح في التسجيل؛ لم يُكتب أي نص.")
        cleaned = clean_text(raw) if s.cleanup_enabled else raw.strip()
        if is_meaningless(cleaned):
            raise NoSpeech("لم يُكتشف كلام مفهوم في التسجيل؛ لم يُكتب أي نص.")
        meta = dict(audio_seconds=round(res.duration, 2), speech_seconds=round(res.speech_duration, 2),
                    process_seconds=round(res.process_seconds, 2), device=res.device,
                    model=self.transcriber.info.model_name if self.transcriber.info else "",
                    fallback=self._groq_notice or res.fallback_reason,
                    dropped_segments=res.dropped_segments)
        log.info("تفريغ: صوت=%.1fث كلام=%.1fث معالجة=%.2fث جهاز=%s", res.duration, res.speech_duration,
                 res.process_seconds, res.device)
        return ProcessOutput(raw, cleaned, meta)
