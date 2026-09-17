import numpy as np
import pytest

from qool.audio import audio_stats, resample
from qool.hotkey import MOD_ALT, MOD_CONTROL, HotkeyError, normalize_hotkey, parse_hotkey


def test_parse_default_hotkey():
    assert parse_hotkey("Ctrl+Alt+Space") == (MOD_CONTROL | MOD_ALT, 0x20)
    assert parse_hotkey("ctrl + alt + q") == (MOD_CONTROL | MOD_ALT, ord("Q"))


def test_reject_plain_keys_and_garbage():
    for bad in ["A", "Space", "", "Ctrl+Alt", "Ctrl+Foo", "Ctrl+A+B"]:
        with pytest.raises(HotkeyError):
            parse_hotkey(bad)
    assert parse_hotkey("F9")[1] == 0x78


def test_normalize():
    assert normalize_hotkey("alt+ctrl+space") == "Ctrl+Alt+Space"
    assert normalize_hotkey("ctrl+alt+q") == "Ctrl+Alt+Q"


def test_resample_48k_to_16k_keeps_duration_and_tone():
    t = np.arange(48000) / 48000
    x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    y = resample(x, 48000)
    assert abs(len(y) - 16000) <= 1
    assert 0.3 < np.max(np.abs(y[1000:-1000])) < 0.6


def test_missing_microphone_gives_clear_error():
    from qool.audio import AudioError, Recorder

    r = Recorder()
    with pytest.raises(AudioError, match="غير موجود"):
        r.start("ميكروفون غير موجود إطلاقًا XYZ", 5)
    assert not r.is_recording


def test_audio_stats_silence():
    s = audio_stats(np.zeros(16000, dtype=np.float32))
    assert s.duration == 1.0 and s.peak == 0.0
