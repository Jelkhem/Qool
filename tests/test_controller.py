import threading

import numpy as np
import pytest

from qool.config import Settings
from qool.controller import Controller, NoSpeech, ProcessOutput, State
from qool.paster import Delivery, Outcome, Paster


class FakeRecorder:
    def __init__(self, audio=None):
        self.audio = audio if audio is not None else (np.sin(np.arange(16000 * 2) / 5) * 0.2).astype(np.float32)
        self.recording = False
        self.starts = 0

    def start(self, device, max_seconds):
        assert not self.recording
        self.recording = True
        self.starts += 1

    def stop(self):
        self.recording = False
        return self.audio

    def abort(self):
        self.recording = False


class ManualWorker:
    """يحتفظ بالمهام لتنفيذها يدويًا لمحاكاة تفريغ بطيء."""

    def __init__(self):
        self.jobs = []

    def __call__(self, fn):
        self.jobs.append(fn)

    def run_all(self):
        jobs, self.jobs = self.jobs, []
        for j in jobs:
            j()


def make(process=None, deliver=None, recorder=None, target="T1"):
    events = []
    delivered = []
    worker = ManualWorker()

    def default_deliver(text, target, auto, should_abort=lambda: False):
        if should_abort():
            return Delivery(Outcome.TARGET_CHANGED, "aborted")
        delivered.append((text, target, auto))
        return Delivery(Outcome.PASTED, "ok")

    c = Controller(
        recorder=recorder or FakeRecorder(),
        capture_target=lambda: target,
        process_audio=process or (lambda audio, cancel_check: ProcessOutput("raw", "clean.", {})),
        deliver=deliver or default_deliver,
        submit=worker,
        settings=Settings(),
        emit=lambda kind, **kw: events.append((kind, kw)),
        same_target=lambda a, b: a == b,
    )
    return c, worker, events, delivered


def test_full_flow_pastes_once():
    c, worker, events, delivered = make()
    c.toggle()
    assert c.state == State.RECORDING
    c.toggle()
    assert c.state == State.PROCESSING
    worker.run_all()
    assert c.state == State.DONE
    assert delivered == [("clean.", "T1", True)]


def test_no_concurrent_processing_or_recording():
    rec = FakeRecorder()
    c, worker, events, delivered = make(recorder=rec)
    c.toggle(); c.toggle()          # يبدأ ثم يوقف -> معالجة
    c.toggle(); c.toggle(); c.toggle()  # ضغطات سريعة أثناء المعالجة
    assert c.state == State.PROCESSING
    assert rec.starts == 1
    assert len(worker.jobs) == 1
    assert not c.start()
    worker.run_all()
    assert len(delivered) == 1


def test_cancel_during_processing_prevents_late_paste():
    c, worker, events, delivered = make()
    c.toggle(); c.toggle()
    c.cancel()
    assert c.state == State.IDLE
    worker.run_all()  # النتيجة تصل متأخرة
    assert delivered == []
    assert c.state == State.IDLE


def test_cancel_then_new_recording_old_result_ignored():
    c, worker, events, delivered = make()
    c.toggle(); c.toggle()
    c.cancel()
    c.toggle(); c.toggle()  # تسجيل جديد
    worker.run_all()        # المهمتان تنفذان
    assert len(delivered) == 1


def test_cancel_signal_reaches_transcription():
    seen = {}

    def process(audio, cancel_check):
        seen["before"] = cancel_check()
        c.cancel()
        seen["after"] = cancel_check()
        return ProcessOutput("r", "c", {})

    c, worker, events, delivered = make(process=process)
    c.toggle(); c.toggle()
    worker.run_all()
    assert seen == {"before": False, "after": True}
    assert delivered == []


def test_silent_audio_not_sent_to_transcription():
    called = []
    rec = FakeRecorder(np.zeros(16000 * 3, dtype=np.float32))
    c, worker, events, delivered = make(recorder=rec, process=lambda a, cancel_check: called.append(1))
    c.toggle(); c.toggle()
    assert worker.jobs == [] and called == []
    assert c.state == State.ERROR


def test_too_short_recording_ignored():
    rec = FakeRecorder((np.ones(1600) * 0.1).astype(np.float32))
    c, worker, events, delivered = make(recorder=rec)
    c.toggle(); c.toggle()
    assert worker.jobs == [] and c.state == State.IDLE


def test_no_speech_shows_error_and_no_paste():
    def process(audio, cancel_check):
        raise NoSpeech("لا كلام")

    c, worker, events, delivered = make(process=process)
    c.toggle(); c.toggle(); worker.run_all()
    assert delivered == [] and c.state == State.ERROR


def test_review_mode_copies_without_paste():
    c, worker, events, delivered = make()
    c.settings.review_mode = True
    c.toggle(); c.toggle(); worker.run_all()
    assert delivered == [("clean.", "T1", False)]


# ---------- Paster مع Win32 وهمي ----------

class FakeWin:
    def __init__(self, fg="T1", clip_fail=False, password=False, elevated=False, send_ok=True, fg_after=None):
        self.fg = fg
        self.fg_after = fg_after
        self.calls = 0
        self.clip = None
        self.clip_fail = clip_fail
        self.password = password
        self.elevated = elevated
        self.send_ok = send_ok
        self.sent = 0

    def set_clipboard_text(self, t):
        if self.clip_fail:
            raise RuntimeError("busy")
        self.clip = t

    def capture_foreground(self):
        self.calls += 1
        if self.fg_after is not None and self.calls > 1:
            return self.fg_after
        return self.fg

    def same_target(self, a, b):
        return a is not None and a == b

    def is_password_field(self, t):
        return self.password

    def is_process_elevated(self, pid):
        return self.elevated

    def current_process_elevated(self):
        return False

    def wait_modifiers_released(self, timeout):
        return True

    def send_ctrl_v(self):
        self.sent += 1
        return self.send_ok, 0 if self.send_ok else 5


class T(str):
    pid = 1

    def describe(self):
        return "notepad.exe"


def test_paster_pastes_into_same_target_once():
    w = FakeWin(fg=T("T1"))
    d = Paster(w).deliver("نص", T("T1"), True)
    assert d.outcome == Outcome.PASTED and w.sent == 1 and w.clip == "نص"


def test_paster_target_changed_does_not_paste():
    w = FakeWin(fg=T("OTHER"))
    d = Paster(w).deliver("نص", T("T1"), True)
    assert d.outcome == Outcome.TARGET_CHANGED and w.sent == 0 and w.clip == "نص"


def test_paster_target_changes_right_before_send():
    w = FakeWin(fg=T("T1"), fg_after=T("OTHER"))
    d = Paster(w).deliver("نص", T("T1"), True)
    assert d.outcome == Outcome.TARGET_CHANGED and w.sent == 0


def test_paster_clipboard_failure_reports_and_no_paste():
    w = FakeWin(fg=T("T1"), clip_fail=True)
    d = Paster(w).deliver("نص", T("T1"), True)
    assert d.outcome == Outcome.CLIPBOARD_FAILED and w.sent == 0 and not d.in_clipboard


def test_paster_password_and_elevated_blocked():
    assert Paster(FakeWin(fg=T("T1"), password=True)).deliver("x", T("T1"), True).outcome == Outcome.PASSWORD_FIELD
    assert Paster(FakeWin(fg=T("T1"), elevated=True)).deliver("x", T("T1"), True).outcome == Outcome.ELEVATED


def test_paster_send_failure_not_reported_as_success():
    d = Paster(FakeWin(fg=T("T1"), send_ok=False)).deliver("x", T("T1"), True)
    assert d.outcome == Outcome.SEND_FAILED


def test_paster_abort_before_anything():
    w = FakeWin(fg=T("T1"))
    d = Paster(w).deliver("x", T("T1"), True, should_abort=lambda: True)
    assert w.sent == 0 and w.clip is None and d.outcome != Outcome.PASTED
