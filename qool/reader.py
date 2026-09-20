"""القارئ: ياخد النص المظلَّل، يفهمه ويلخّصه بالمصري، ويقراه بصوت عالي أول بأول.

الخيط الواحد بيعمل: تدفق من الموديل ← تقطيع جُمَل ← نطق الجملة ← تشغيلها.
النطق بيسبق التشغيل بجملة، فالكلام بيبدأ خلال ثانيتين تقريبًا من ضغط الاختصار.
"""
from __future__ import annotations

import logging
import re
import threading

import numpy as np

from . import llm, tts
from .audio import AudioError, find_output_device

log = logging.getLogger(__name__)

_END_CHARS = ".!?؟…\n"


def speakable(text: str) -> str:
    """يشيل الماركداون والرموز اللي مش بتتقري بصوت."""
    text = re.sub(r"```[\s\S]*?```", " (كود) ", text)
    text = re.sub(r"`([^`\n]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " (صورة) ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " (رابط) ", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+•]\s+", "", text)
    text = re.sub(r"(?m)^\s*\d+[.)]\s+", "", text)
    # [ \t] وليس \s: \s بتبلع السطر الجديد نفسه فتلزق الصفوف في بعضها
    text = re.sub(r"(?m)^[ \t]*[:\-|][:\-| \t]{2,}[ \t]*$", "", text)   # صفوف فواصل الجداول
    text = re.sub(r"(?m)^[ \t]*\|(.*)\|[ \t]*$",
                  lambda m: m.group(1).strip().replace("|", "، "), text)
    text = re.sub(r"(\*\*|__|~~|\*|_)", "", text)
    text = re.sub(r"[#>•▪◦→←↑↓●○★☆✓✔✗×]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def take_sentences(buf: str, flush: bool = False, min_len: int = 40,
                   max_len: int = 320) -> tuple[list[str], str]:
    """يقسم ما وصل إلى جُمَل كاملة، ويرجّع (الجُمَل، الباقي اللي لسه ناقص)."""
    out: list[str] = []
    start = i = 0
    n = len(buf)
    while i < n:
        ch = buf[i]
        seg = i - start + 1
        ender = ch in _END_CHARS
        if ch == "." and 0 < i < n - 1 and buf[i - 1].isdigit() and buf[i + 1].isdigit():
            ender = False  # رقم عشري زي 3.5
        if ender and seg >= min_len:
            j = i + 1
            while j < n and (buf[j] in _END_CHARS or buf[j] in " \t"):
                j += 1
            if j >= n and not flush:
                break  # مستنيين باقي التدفق للتأكد إن الجملة خلصت
            piece = buf[start:j].strip()
            if piece:
                out.append(piece)
            start = i = j
            continue
        if seg >= max_len and ch == " ":
            piece = buf[start:i].strip()
            if piece:
                out.append(piece)
            start = i + 1
        i += 1
    rest = buf[start:]
    if flush and rest.strip():
        out.append(rest.strip())
        rest = ""
    return out, rest


class Reader:
    """يشتغل في خيط مستقل؛ كل الاستدعاءات من الواجهة غير حاجزة."""

    def __init__(self, settings, on_state, on_text):
        self.settings = settings
        self.on_state = on_state          # (state, message)
        self.on_text = on_text            # (النص المنطوق لحد دلوقتي)
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._stream = None
        self._lock = threading.Lock()
        self._mode = ""      # يتجاوز read_mode لهذه القراءة فقط (معاينة الصوت)

    # ---------- التحكم ----------
    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def start(self, text: str, mode: str = "") -> None:
        if self.is_running():
            self.stop()
        text = (text or "").strip()
        if not text:
            self.on_state("error", "مفيش نص متظلَّل. ظلّل الكلام الأول وبعدين اضغط الاختصار.")
            return
        self._mode = mode
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(text,), name="reader", daemon=True)
        self._thread.start()

    def stop(self, message: str = "وقفت القراءة.") -> None:
        self._cancel.set()
        with self._lock:
            stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._thread = None
        self.on_state("off", message)

    # ---------- التنفيذ ----------
    def _run(self, text: str) -> None:
        s = self.settings
        try:
            self.on_state("thinking", "بيقرا النص..." if self.mode == "raw" else "بيفهم ويلخّص...")
            spoken: list[str] = []

            for sentence in self._sentences(text):
                if self._cancel.is_set():
                    return
                pcm = tts.synth(sentence, s.tts_voice, s.tts_rate)
                if self._cancel.is_set():
                    return
                if pcm.size:
                    spoken.append(sentence)
                    self.on_text(" ".join(spoken))
                    self.on_state("speaking", "بيتكلم...")
                    if not self._play(pcm):
                        return
            if self._cancel.is_set():
                return
            self._close_stream()
            self.on_state("off", "خلص." if spoken else "مفيش حاجة تتقال.")
        except (llm.LLMError, tts.TTSError, AudioError) as e:
            self._close_stream()
            if not self._cancel.is_set():
                self.on_state("error", str(e))
        except Exception as e:
            log.exception("فشل غير متوقع في القارئ")
            self._close_stream()
            if not self._cancel.is_set():
                self.on_state("error", f"حصل خطأ غير متوقع: {e}")

    @property
    def mode(self) -> str:
        return self._mode or self.settings.read_mode

    def _sentences(self, text: str):
        """مولّد الجُمَل: من الموديل لو في تلخيص، أو من النص نفسه في وضع «زي ما هو»."""
        if self.mode == "raw":
            clean = speakable(text)
            sents, rest = take_sentences(clean, flush=True)
            yield from sents
            if rest.strip():
                yield rest.strip()
            return

        model = self._model()
        buf = ""
        for piece in self._stream_llm(text, model):
            buf += piece
            sents, buf = take_sentences(buf)
            for sent in sents:
                yield speakable(sent)
            if self._cancel.is_set():
                return
        sents, _ = take_sentences(buf, flush=True)
        for sent in sents:
            yield speakable(sent)

    def _stream_llm(self, text: str, model: str):
        s = self.settings
        key = s.api_key()
        try:
            yield from llm.stream_explain(text, api_key=key, model=model, mode=self.mode,
                                          cancel=self._cancel)
        except llm.ModelMissing:
            # الموديل المحفوظ اتشال أو مش متاح للمفتاح — أعِد الاختيار تلقائيًا مرة واحدة.
            fresh = llm.resolve_model(key, llm.AUTO)
            log.info("الموديل %s مش متاح؛ تحوّلنا إلى %s", model, fresh)
            self.settings.llm_model = fresh
            self.settings.save()
            yield from llm.stream_explain(text, api_key=key, model=fresh, mode=self.mode,
                                          cancel=self._cancel)

    def _model(self) -> str:
        s = self.settings
        if s.llm_model and s.llm_model != llm.AUTO:
            return s.llm_model
        model = llm.resolve_model(s.api_key(), llm.AUTO)
        s.llm_model = model
        s.save()
        return model

    # ---------- التشغيل الصوتي ----------
    def _open_stream(self):
        with self._lock:
            if self._stream is not None:
                return self._stream
        import sounddevice as sd

        device = find_output_device(self.settings.tts_output_device)
        stream = sd.OutputStream(device=device, samplerate=tts.RATE, channels=1, dtype="float32",
                                 blocksize=0)
        stream.start()
        with self._lock:
            self._stream = stream
        return stream

    def _close_stream(self):
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _play(self, pcm: np.ndarray) -> bool:
        """يكتب الموجة على الجهاز على دفعات صغيرة عشان «إيقاف» يستجيب فورًا."""
        stream = self._open_stream()
        step = 2048
        for i in range(0, len(pcm), step):
            if self._cancel.is_set():
                return False
            try:
                stream.write(pcm[i:i + step])
            except Exception as e:
                if self._cancel.is_set():
                    return False
                raise AudioError(f"تعذّر تشغيل الصوت ({e}).") from None
        return True
