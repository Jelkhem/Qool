"""القارئ: ياخد النص المظلَّل، يفهمه ويلخّصه بالمصري، ويقراه بصوت عالي أول بأول.

خيطين: واحد بيولّد (تدفق من الموديل ← تقطيع ← نطق) وواحد بيشغّل الصوت.
الفصل ده مهم لأن التوليد والتشغيل لازم يمشوا مع بعض؛ لو اتبادلوا هيبقى في سكات
بين كل قطعة والتانية. وصوت Gemini بيوصل على أجزاء صغيرة، فالكلام بيبدأ خلال
حوالي ٣ ثواني من ضغط الاختصار.
"""
from __future__ import annotations

import logging
import queue
import re
import threading

import numpy as np

from . import llm, tts
from .audio import AudioError, find_output_device

log = logging.getLogger(__name__)

_END_CHARS = ".!?؟…\n"
_PREBUFFER_SECONDS = 0.5      # نجمّع شوية صوت قبل ما نبدأ عشان ما يتقطّعش
_QUEUE_CHUNKS = 120           # سقف الانتظار (~دقيقة صوت) عشان الذاكرة ما تكبرش


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


def coalesce(pieces, seconds: float = _PREBUFFER_SECONDS):
    """يجمّع أجزاء الـ٤٠ مللي اللي جاية من Gemini في قطع نصف ثانية:
    كتابات أقل على كارت الصوت، ومخزون صغير يمنع التقطيع."""
    need = int(seconds * tts.RATE)
    buf: list[np.ndarray] = []
    have = 0
    for piece in pieces:
        if piece.size == 0:
            continue
        buf.append(piece)
        have += piece.size
        if have >= need:
            yield np.concatenate(buf)
            buf, have = [], 0
    if buf:
        yield np.concatenate(buf)


class Reader:
    """يشتغل في خيوط مستقلة؛ كل الاستدعاءات من الواجهة غير حاجزة."""

    MAX_MODEL_TRIES = 3

    def __init__(self, settings, on_state, on_text):
        self.settings = settings
        self.on_state = on_state          # (state, message)
        self.on_text = on_text            # (النص المنطوق لحد دلوقتي)
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._stream = None
        self._lock = threading.Lock()
        self._mode = ""               # يتجاوز read_mode لهذه القراءة فقط (معاينة الصوت)
        self._edge_fallback = False   # صوت Gemini وقع، فبنكمّل على Edge
        self._gemini_voice = False    # بيتحدد مرة واحدة أول كل قراءة
        self._voice_models: list[str] = []   # موديلات الصوت اللي لسه عندها رصيد

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
        self._edge_fallback = False
        cancel = threading.Event()
        self._cancel = cancel
        self._thread = threading.Thread(target=self._run, args=(text, cancel), name="reader", daemon=True)
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

    @property
    def mode(self) -> str:
        return self._mode or self.settings.read_mode

    @property
    def dialect(self) -> str:
        """صوت Gemini بينطق العامية صح؛ أصوات Edge متدرّبة على الفصحى،
        فلو هنقرا بـEdge لازم النص نفسه يتكتب فصحى من الأول."""
        return "masri" if self._gemini_voice else "fosha"

    def _pick_voice_engine(self) -> None:
        s = self.settings
        self._voice_models = []
        if s.tts_engine == "gemini" and s.api_key() and s.voice_budget_left():
            # الاكتشاف من الـAPI: أي موديل صوت جديد بيتحسب لوحده بـ١٠ طلبات زيادة
            self._voice_models = s.live_voice_models(tts.discover_models(s.api_key()))
            if not self._voice_models:
                s.note_voice_exhausted()
        self._gemini_voice = bool(self._voice_models)
        self._edge_fallback = not self._gemini_voice
        if s.tts_engine == "gemini" and not self._gemini_voice and s.api_key():
            self.on_state("thinking", "خلصت حصة صوت Gemini النهاردة — هنقرا بصوت Edge بالفصحى.")

    # ---------- التشغيل (الخيط الرئيسي للقراءة) ----------
    def _run(self, text: str, cancel: threading.Event) -> None:
        q: queue.Queue = queue.Queue(maxsize=_QUEUE_CHUNKS)
        producer = threading.Thread(target=self._produce, args=(text, q, cancel),
                                    name="reader-synth", daemon=True)
        try:
            self.on_state("thinking", "بيقرا النص..." if self.mode == "raw" else "بيفهم ويلخّص...")
            producer.start()
            heard = False
            while True:
                item = q.get()
                if item is None or cancel.is_set():
                    break
                kind, payload = item
                if kind == "error":
                    raise payload
                if kind == "text":
                    self.on_text(payload)
                    continue
                if payload.size:
                    if not heard:
                        heard = True
                        self.on_state("speaking", "بيتكلم...")
                    if not self._play(payload, cancel):
                        break
            if not cancel.is_set():
                self._drain()
                self.on_state("off", "خلص." if heard else "مفيش حاجة تتقال.")
        except (llm.LLMError, tts.TTSError, AudioError) as e:
            if not cancel.is_set():
                self.on_state("error", str(e))
        except Exception as e:
            log.exception("فشل غير متوقع في القارئ")
            if not cancel.is_set():
                self.on_state("error", f"حصل خطأ غير متوقع: {e}")
        finally:
            cancel.set()                # يفكّ المولّد لو كان مستني مكان في الطابور
            # لو قراءة جديدة بدأت وإحنا لسه بنخلّص، ما نقفلش مجرى صوتها
            if self._cancel is cancel:
                self._close_stream()

    # ---------- التوليد (الخيط التاني) ----------
    def _produce(self, text: str, q: queue.Queue, cancel: threading.Event) -> None:
        """يولّد الصوت ويحطّه في الطابور وهو بيتقال، فالتوليد دايمًا سابق التشغيل."""
        spoken: list[str] = []
        try:
            self._pick_voice_engine()
            for sentence in self._sentences(text, cancel):
                if cancel.is_set():
                    return
                spoken.append(sentence)
                self._put(q, ("text", " ".join(spoken)), cancel)
                for pcm in self._synth(sentence, cancel):
                    if cancel.is_set():
                        return
                    self._put(q, ("pcm", pcm), cancel)
        except Exception as e:
            self._put(q, ("error", e), cancel)
        finally:
            self._put(q, None, cancel)

    def _put(self, q: queue.Queue, item, cancel: threading.Event) -> None:
        """حطّ في الطابور من غير ما تعلّق لو التشغيل وقف."""
        while not cancel.is_set():
            try:
                q.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def _synth(self, sentence: str, cancel: threading.Event):
        """أجزاء صوت لقطعة. Gemini بيدفّق فبنشغّل وهو بيولّد؛ Edge بيرجّع مرة واحدة."""
        s = self.settings
        if self._gemini_voice:
            try:
                s.note_voice_call()
                yield from coalesce(tts.stream_gemini(
                    sentence, s.gemini_voice, s.api_key(),
                    style=s.speech_style or tts.DEFAULT_STYLE, cancel=cancel,
                    models=self._voice_models, on_exhausted=s.note_voice_model_dead))
                return
            except llm.QuotaError as e:
                # خلصت كل موديلات الصوت النهاردة — بلاش نحاول تاني لحد بكرة
                s.note_voice_exhausted()
                self._gemini_voice = self._edge_fallback = False
                log.warning("خلصت حصة صوت Gemini (%s)؛ كمّلنا على Edge", e)
                self.on_state("speaking", "خلصت حصة صوت Gemini النهاردة — كمّلنا بصوت Edge.")
            except (llm.Overloaded, tts.TTSError) as e:
                self._gemini_voice = False
                log.warning("صوت Gemini وقع (%s)؛ كمّلنا على Edge", e)
                self.on_state("speaking", f"صوت Gemini مش متاح ({e}) — كمّلنا بصوت Edge.")
        yield tts.synth(sentence, s.tts_voice, s.tts_rate)

    def _chunk_size(self, first: bool) -> tuple[int, int]:
        """أول قطعة صغيرة عشان الكلام يبدأ بدري، وبعدها الباقي كله في قطعة واحدة.

        كل قطعة طلب صوت مستقل، والحد المجاني ١٠ طلبات في اليوم **لكل موديل**.
        قطعتين للقراءة × موديلين صوت = حوالي ١٠ قراءات في اليوم، وأول صوت في ~٤ ثواني.
        الانتظار للنص كله عشان نوفّر طلب واحد كان بيوصّل أول صوت لـ٢٤ ثانية — مش مستاهل.
        """
        if self._gemini_voice:
            return (60, 220) if first else (400, 4000)
        return (40, 320)   # Edge مجاني بلا حد، فبنقطّع أكتر عشان يبدأ أسرع

    def _sentences(self, text: str, cancel: threading.Event):
        """مولّد القطع: من الموديل لو في تلخيص، أو من النص نفسه في وضع «زي ما هو»."""
        first = True
        if self.mode == "raw":
            buf = speakable(text)
            while buf:
                mn, mx = self._chunk_size(first)
                sents, buf = take_sentences(buf, flush=True, min_len=mn, max_len=mx)
                if not sents:
                    break
                for sent in sents:
                    yield sent
                    first = False
            return

        model = self._model()
        buf = ""
        for piece in self._stream_llm(text, model):
            buf += piece
            mn, mx = self._chunk_size(first)
            sents, buf = take_sentences(buf, min_len=mn, max_len=mx)
            for sent in sents:
                yield speakable(sent)
                first = False
            if cancel.is_set():
                return
        mn, mx = self._chunk_size(first)
        sents, _ = take_sentences(buf, flush=True, min_len=mn, max_len=mx)
        for sent in sents:
            yield speakable(sent)

    def _stream_llm(self, text: str, model: str, cancel: threading.Event):
        """الموديلات المجانية بتوقع تحت الضغط، فبنجرّب غيرها — بس قبل ما يتقال أي كلام."""
        key = self.settings.api_key()
        tried = [model]
        pending: list[str] = []
        said_something = False
        while True:
            try:
                for piece in llm.stream_explain(text, api_key=key, model=tried[-1],
                                                mode=self.mode, dialect=self.dialect,
                                                cancel=cancel):
                    said_something = True
                    yield piece
                if tried[-1] != self.settings.llm_model:
                    self.settings.llm_model = tried[-1]   # فضّل اللي نجح المرة الجاية
                    self.settings.save()
                return
            except (llm.ModelMissing, llm.Overloaded) as e:
                # لو اتقال نص الكلام، إعادة الطلب هتكرّره على المستخدم — فنوقف.
                if said_something or len(tried) >= self.MAX_MODEL_TRIES:
                    raise
                if not pending:
                    pending = llm.fallback_models(key, set(tried))
                if not pending:
                    raise
                nxt = pending.pop(0)
                log.info("الموديل %s وقع (%s)؛ بنجرّب %s", tried[-1], e, nxt)
                self.on_state("thinking", f"الموديل عليه ضغط؛ بنجرّب {nxt}...")
                tried.append(nxt)

    def _model(self) -> str:
        s = self.settings
        if s.llm_model and s.llm_model != llm.AUTO:
            return s.llm_model
        model = llm.resolve_model(s.api_key(), llm.AUTO)
        s.llm_model = model
        s.save()
        return model

    # ---------- الإخراج الصوتي ----------
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

    def _drain(self):
        """يستنى آخر صوت في كارت الصوت يخلص قبل ما نقول «خلص»."""
        with self._lock:
            stream = self._stream
        if stream is not None:
            try:
                stream.stop()   # stop (مش abort) بيشغّل اللي في المخزن للآخر
            except Exception:
                pass

    def _close_stream(self):
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _play(self, pcm: np.ndarray, cancel: threading.Event) -> bool:
        """يكتب الموجة على الجهاز على دفعات صغيرة عشان «إيقاف» يستجيب فورًا."""
        stream = self._open_stream()
        step = 2048
        for i in range(0, len(pcm), step):
            if cancel.is_set():
                return False
            try:
                stream.write(pcm[i:i + step])
            except Exception as e:
                if cancel.is_set():
                    return False
                raise AudioError(f"تعذّر تشغيل الصوت ({e}).") from None
        return True
