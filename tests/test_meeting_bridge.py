import os
import time
from dataclasses import dataclass

import numpy as np
import pytest

from qool import meeting_bridge as mb


def test_ring_pads_missing_audio_with_silence():
    r = mb.Ring()
    r.push(np.ones(100, dtype=np.float32))
    out = r.pull(300)
    assert np.all(out[:100] == 1) and np.all(out[100:] == 0)
    assert r.buffered == 0


def test_ring_partial_pull_keeps_order():
    r = mb.Ring()
    r.push(np.arange(10, dtype=np.float32))
    assert list(r.pull(4)) == [0, 1, 2, 3]
    assert list(r.pull(6)) == [4, 5, 6, 7, 8, 9]


def test_ring_keeps_latency_bounded_and_clears():
    r = mb.Ring()
    r.push(np.ones(mb.MAX_LAG + 1, dtype=np.float32))
    assert r.buffered == mb.KEEP_LAG
    r.clear()
    assert r.buffered == 0 and np.all(r.pull(10) == 0)


def test_live_level_drops_when_device_goes_quiet():
    r = mb.Ring()
    r.push(np.full(480, 0.5, dtype=np.float32))
    assert r.live_level > 0.4
    r.last_push -= 1.0
    assert r.live_level == 0.0


def test_meter_scale():
    assert mb.meter(0.0) == 0.0
    assert mb.meter(1.0) == 1.0
    assert 0.4 < mb.meter(10 ** (-30 / 20)) < 0.6


def test_ducker_mutes_while_ai_talks_plus_hangover():
    d = mb.Ducker(threshold=0.01, hangover=0.35)
    assert not d.update(0.0, 0.0)
    assert d.update(0.2, 1.0)
    assert d.update(0.0, 1.3)      # ذيل الكلام
    assert not d.update(0.0, 1.4)


def _fake_inputs(monkeypatch, devices, default):
    monkeypatch.setattr(mb, "_wasapi_devices", lambda kind: devices)
    monkeypatch.setattr(mb, "_default_input", lambda: default)


def test_mic_never_picks_the_cable(monkeypatch):
    _fake_inputs(monkeypatch, [(1, "CABLE Output (VB-Audio Virtual Cable)"), (2, "Jack Mic (Realtek)")], default=1)
    assert mb.pick_mic("") == (2, "Jack Mic (Realtek)")
    with pytest.raises(mb.BridgeError, match="مايك حقيقي"):
        mb.pick_mic("CABLE Output")


def test_mic_uses_windows_default_and_reports_missing(monkeypatch):
    _fake_inputs(monkeypatch, [(2, "Jack Mic (Realtek)"), (3, "Microphone Array")], default=3)
    assert mb.pick_mic("") == (3, "Microphone Array")
    with pytest.raises(mb.BridgeError, match="مش متوصل"):
        mb.pick_mic("Polycom")


def test_missing_cable_gives_clear_error(monkeypatch):
    monkeypatch.setattr(mb, "_wasapi_devices", lambda kind: [(5, "Speakers (Realtek)")])
    with pytest.raises(mb.BridgeError, match="VB-Audio"):
        mb.pick_output()


def test_missing_app_is_none():
    assert mb.find_app_pid("no-such-app-xyz-123.exe") is None


def test_find_app_pid_is_fast_and_finds_running_process():
    exe = next(name for pid, _, name in mb._processes() if pid == os.getpid())
    start = time.perf_counter()
    assert mb.find_app_pid(exe) is not None
    assert time.perf_counter() - start < 1.0  # بيتنادى من خيط الواجهة


# ---------- اكتشاف جهاز Teams ----------

@dataclass
class FakeSession:
    device_id: str
    device_name: str
    pid: int
    state: int

    @property
    def active(self):
        return self.state == 1


# explorer(1) ← ms-teams(10) ← ms-teams(11) / msedgewebview2(12) ; ChatGPT(20)
PROCS = {1: (0, "explorer.exe"), 10: (1, "ms-teams.exe"), 11: (10, "ms-teams.exe"),
         12: (10, "msedgewebview2.exe"), 20: (1, "ChatGPT.exe")}


def test_owner_names_walks_parents():
    assert mb.owner_names(12, PROCS) == {"msedgewebview2.exe", "ms-teams.exe", "explorer.exe"}


def test_meeting_device_prefers_active_teams_session():
    sessions = [FakeSession("hp", "Headphones (Realtek)", 11, 0),
                FakeSession("sp", "Speakers (Realtek)", 11, 1),
                FakeSession("hp", "Headphones (Realtek)", 20, 1)]
    assert mb.choose_meeting_device(sessions, PROCS) == ("sp", "Speakers (Realtek)")


def test_meeting_device_refuses_teams_on_cable():
    sessions = [FakeSession("cable", "CABLE Input (VB-Audio Virtual Cable)", 11, 1),
                FakeSession("hp", "Headphones (Realtek)", 11, 0)]
    with pytest.raises(mb.BridgeError, match="للهيدفون"):
        mb.choose_meeting_device(sessions, PROCS)


def test_meeting_device_falls_back_to_inactive_then_none():
    sessions = [FakeSession("cable", "CABLE Input (VB-Audio Virtual Cable)", 11, 0),
                FakeSession("hp", "Headphones (Realtek)", 12, 0)]
    assert mb.choose_meeting_device(sessions, PROCS) == ("hp", "Headphones (Realtek)")
    assert mb.choose_meeting_device([FakeSession("hp", "Headphones", 20, 1)], PROCS) is None
