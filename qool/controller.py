"""آلة الحالات للإملاء — بلا Qt حتى تُختبر آليًا.

القواعد:
- تسجيل واحد ومعالجة واحدة فقط في كل مرة.
- كل عملية لها رقم job؛ الإلغاء يزيد الرقم فتُهمَل أي نتيجة متأخرة ولا تُلصق.
- النافذة المستهدفة تُسجَّل عند بدء الإملاء، ويُتحقق منها قبل اللصق.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .audio import AudioError, audio_stats
from .paster import Delivery, Outcome

log = logging.getLogger(__name__)

MIN_DURATION_S = 0.4
SILENT_PEAK = 0.003  # ~ -50 dBFS: لا يوجد صوت ملتقط تقريبًا


class State(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


@dataclass
class ProcessOutput:
    raw_text: str
    clean_text: str
    info: dict


class NoSpeech(Exception):
    """لا يوجد كلام مفهوم؛ الرسالة للمستخدم."""


class Controller:
    def __init__(self, *, recorder, capture_target: Callable[[], Any], process_audio: Callable[..., ProcessOutput],
                 deliver: Callable[..., Delivery], submit: Callable[[Callable[[], None]], None],
                 settings, emit: Callable[..., None], same_target: Callable[[Any, Any], bool] | None = None):
        self.recorder = recorder
        self.capture_target = capture_target
        self.process_audio = process_audio
        self.deliver = deliver
        self.submit = submit
        self.settings = settings
        self.emit = emit
        self.same_target = same_target
        self._lock = threading.RLock()
        self.state = State.IDLE
        self._job = 0
        self._target = None
        self.last_output: ProcessOutput | None = None
        self.last_target = None

    # ---------- مساعدات ----------
    def _set_state(self, state: State, message: str = "") -> None:
        self.state = state
        log.info("حالة: %s", state.value)  # اسم الحالة فقط، دون أي نص
        self.emit("state", state=state, message=message)

    @property
    def busy(self) -> bool:
        return self.state in (State.RECORDING, State.PROCESSING)

    # ---------- أوامر ----------
    def toggle(self) -> None:
        with self._lock:
            if self.state == State.RECORDING:
                self.stop()
            elif self.state == State.PROCESSING:
                self.emit("notice", message="جارٍ معالجة التسجيل السابق… انتظر أو اضغط إلغاء.")
            else:
                self.start()

    def start(self) -> bool:
        with self._lock:
            if self.busy:
                return False
            self._target = self.capture_target()
            try:
                self.recorder.start(self.settings.microphone, self.settings.max_recording_minutes * 60)
            except AudioError as e:
                self._set_state(State.ERROR, str(e))
                return False
            except Exception as e:
                log.exception("فشل بدء التسجيل")
                self._set_state(State.ERROR, f"تعذر بدء التسجيل: {e}")
                return False
            self._job += 1
            self._set_state(State.RECORDING, "جارٍ التسجيل… اضغط الاختصار مرة أخرى للإيقاف.")
            return True

    def stop(self, reason: str = "") -> bool:
        with self._lock:
            if self.state != State.RECORDING:
                return False
            audio = self.recorder.stop()
            stats = audio_stats(audio)
            target, job = self._target, self._job
            if stats.duration < MIN_DURATION_S:
                self._set_state(State.IDLE, "التسجيل قصير جدًا؛ لم تتم معالجته.")
                return False
            if stats.peak < SILENT_PEAK:
                self._set_state(State.ERROR, "لم يُلتقط أي صوت. تأكد أن الميكروفون غير مكتوم وأنه الجهاز الصحيح.")
                return False
            self._set_state(State.PROCESSING, (reason + " " if reason else "") + "جارٍ تحويل الكلام إلى نص…")
        self.submit(lambda: self._process(job, audio, target, stats))
        return True

    def cancel(self) -> None:
        with self._lock:
            if self.state == State.RECORDING:
                self.recorder.abort()
            elif self.state != State.PROCESSING:
                return
            self._job += 1
            self._set_state(State.IDLE, "تم الإلغاء.")

    def handle_recorder_problem(self, kind: str) -> None:
        """يُستدعى من خيط الواجهة (وليس خيط الصوت)."""
        if kind == "limit":
            self.stop("وصل التسجيل للحد الأقصى للمدة.")
        elif kind == "device_lost":
            with self._lock:
                if self.state == State.RECORDING:
                    if not self.stop("انقطع الميكروفون؛ ستتم معالجة ما سُجِّل.") and self.state == State.RECORDING:
                        self._set_state(State.ERROR, "انقطع الميكروفون.")

    # ---------- العامل ----------
    def _is_current(self, job: int) -> bool:
        return job == self._job

    def _process(self, job: int, audio, target, stats) -> None:
        try:
            out = self.process_audio(audio, cancel_check=lambda: not self._is_current(job))
        except NoSpeech as e:
            with self._lock:
                if self._is_current(job):
                    self._set_state(State.ERROR, str(e))
            return
        except Exception as e:
            with self._lock:
                if not self._is_current(job):
                    return
                if type(e).__name__ == "TranscriptionCancelled":
                    return
                log.exception("فشل التفريغ")
                self._set_state(State.ERROR, f"حدث خطأ أثناء التفريغ: {e}")
            return
        with self._lock:
            if not self._is_current(job) or self.state != State.PROCESSING:
                log.info("تم تجاهل نتيجة عملية ملغاة")
                return
            self.last_output, self.last_target = out, target
            auto = self.settings.auto_paste and not self.settings.review_mode
            delivery = self.deliver(out.clean_text, target, auto,
                                    should_abort=lambda: not self._is_current(job))
            log.info("نتيجة التسليم: %s (الهدف: %s)", delivery.outcome.value,
                     getattr(target, "process_name", "") or "لا يوجد")
            self.emit("result", output=out, delivery=delivery)
            ok = delivery.outcome in (Outcome.PASTED, Outcome.COPIED_ONLY)
            self._set_state(State.DONE if ok else State.ERROR if delivery.outcome == Outcome.CLIPBOARD_FAILED else State.DONE,
                            delivery.message)

    # ---------- إعادة محاولة اللصق ----------
    def retry_paste(self, text: str, wait_seconds: float = 6.0, capture=None, sleep=time.sleep) -> None:
        """ينتظر عودة المستخدم إلى النافذة الأصلية (دون إجبار التركيز) ثم يلصق مرة واحدة."""
        target = self.last_target
        if not text:
            return
        if target is None or self.same_target is None:
            d = self.deliver(text, None, False)
            self.emit("notice", message=d.message)
            return
        capture = capture or self.capture_target
        self.emit("notice", message="انقر داخل خانة الكتابة الأصلية خلال بضع ثوانٍ وسيتم اللصق…")
        end = time.monotonic() + wait_seconds
        while time.monotonic() < end:
            if self.same_target(target, capture()):
                d = self.deliver(text, target, True)
                self.emit("notice", message=d.message)
                return
            sleep(0.1)
        d = self.deliver(text, None, False)
        self.emit("notice", message="لم تتم العودة للنافذة الأصلية. " + d.message)
