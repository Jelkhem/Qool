"""التفريغ الأونلاين على Groq: بناء الطلب، قراءة الرد، والرجوع للجهاز عند الفشل."""
import io
import json
import urllib.error
import wave
from unittest.mock import patch

import numpy as np
import pytest

from qool import groq_stt
from qool.cleanup import Segment
from qool.config import Settings
from qool.pipeline import Pipeline

KEY = "gsk_" + "t" * 48
AUDIO = np.sin(np.linspace(0, 400, 16000 * 3)).astype(np.float32) * 0.3


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _resp(payload: dict) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def _http_error(code: int, message: str = "nope") -> urllib.error.HTTPError:
    body = json.dumps({"error": {"message": message}}).encode()
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(body))


def test_wav_bytes_is_valid_16bit_mono():
    data = groq_stt.wav_bytes(AUDIO)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.getnframes() == len(AUDIO)


def test_request_carries_key_model_and_audio():
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = req.data
        return _resp({"segments": [{"text": "أهلاً", "start": 0.0, "end": 1.0}]})

    with patch("urllib.request.urlopen", fake_urlopen):
        segments, took = groq_stt.transcribe(AUDIO, api_key=KEY, model="whisper-large-v3-turbo",
                                             prompt="سياق")
    assert seen["url"].endswith("/audio/transcriptions")
    assert seen["auth"] == f"Bearer {KEY}"
    assert b'name="model"' in seen["body"] and b"whisper-large-v3-turbo" in seen["body"]
    assert b'name="language"' in seen["body"]
    assert b"fLaC" in seen["body"] or b"RIFF" in seen["body"]   # مضغوط أو خام
    assert segments == [Segment("أهلاً", 0.0, 1.0)] and took >= 0


def test_requests_carry_a_user_agent():
    """بدون User-Agent صريح بيرد Cloudflare بـ403 (code 1010) حتى لو المفتاح سليم."""
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen.setdefault("agents", []).append(req.get_header("User-agent"))
        return _resp({"text": "ok", "data": []})

    with patch("urllib.request.urlopen", fake_urlopen):
        groq_stt.transcribe(AUDIO, api_key=KEY)
        groq_stt.check_key(KEY)
    assert seen["agents"] == [groq_stt.USER_AGENT] * 2
    assert "urllib" not in groq_stt.USER_AGENT.lower()


def test_plain_text_response_still_returns_one_segment():
    with patch("urllib.request.urlopen", lambda *a, **k: _resp({"text": "كلام"})):
        segments, _ = groq_stt.transcribe(AUDIO, api_key=KEY)
    assert [s.text for s in segments] == ["كلام"]


def test_missing_key_is_rejected_before_any_request():
    with patch("urllib.request.urlopen", lambda *a, **k: pytest.fail("ما كانش المفروض يتصل")):
        with pytest.raises(groq_stt.GroqAuthError):
            groq_stt.transcribe(AUDIO, api_key="")


def test_oversized_audio_is_rejected_locally(monkeypatch):
    monkeypatch.setattr(groq_stt, "MAX_UPLOAD_MB", 0.001)
    with patch("urllib.request.urlopen", lambda *a, **k: pytest.fail("ما كانش المفروض يرفع")):
        with pytest.raises(groq_stt.GroqError, match="حد الرفع"):
            groq_stt.transcribe(AUDIO, api_key=KEY)


def test_long_audio_is_compressed_enough_to_upload():
    """تسجيل ١٥ دقيقة كـWAV = ~٢٧ MB (فوق الحد)؛ لازم الضغط ينزّله تحته."""
    t = np.linspace(0, 900, 16000 * 900, dtype=np.float32)
    speech = (0.3 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 2.3 * t))).astype(np.float32)
    wav_mb = len(groq_stt.wav_bytes(speech)) / 2**20
    flac = groq_stt.flac_bytes(speech)
    assert flac is not None, "الضغط لازم يشتغل (av مثبتة)"
    flac_mb = len(flac) / 2**20
    assert wav_mb > groq_stt.MAX_UPLOAD_MB, "الاختبار نفسه بايظ لو الـWAV بقى تحت الحد"
    assert flac_mb < groq_stt.MAX_UPLOAD_MB, f"المضغوط {flac_mb:.1f} MB لسه فوق الحد"


def test_falls_back_to_wav_when_compression_fails(monkeypatch):
    monkeypatch.setattr(groq_stt, "flac_bytes", lambda audio, rate=16000: None)
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["body"] = req.data
        return _resp({"text": "ok"})

    with patch("urllib.request.urlopen", fake_urlopen):
        groq_stt.transcribe(AUDIO, api_key=KEY)
    assert b"RIFF" in seen["body"] and b'filename="audio.wav"' in seen["body"]


@pytest.mark.parametrize("code,exc", [(401, groq_stt.GroqAuthError), (429, groq_stt.GroqQuotaError),
                                      (500, groq_stt.GroqError), (413, groq_stt.GroqError)])
def test_http_errors_become_arabic_messages(code, exc):
    def boom(*a, **k):
        raise _http_error(code)

    with patch("urllib.request.urlopen", boom):
        with pytest.raises(exc) as e:
            groq_stt.transcribe(AUDIO, api_key=KEY)
    assert str(e.value) and not str(e.value).startswith("HTTP")


def _settings(**kw) -> Settings:
    kw.setdefault("stt_backend", "groq")
    kw.setdefault("groq_fallback_local", True)
    s = Settings(**kw)
    s.set_groq_key(KEY)
    return s


def test_pipeline_uses_groq_and_skips_local_model():
    s = _settings()
    p = Pipeline(s)
    with patch.object(groq_stt, "transcribe", return_value=([Segment("كلام مفهوم أهو", 0.0, 2.0)], 0.4)), \
         patch.object(Pipeline, "load", side_effect=AssertionError("ما كانش المفروض يحمّل نموذج محلي")):
        out = p.process_audio(AUDIO)
    assert out.info["device"] == "groq"
    assert "كلام مفهوم" in out.clean_text


def test_pipeline_falls_back_to_local_when_groq_fails(monkeypatch):
    s = _settings()
    p = Pipeline(s)
    local = [False]

    def fake_local(audio, **kw):
        local[0] = True
        from qool.transcriber import TranscriptResult
        return TranscriptResult([Segment("النص من الجهاز", 0.0, 2.0)], 2.0, 2.0, 1.0, "cpu")

    monkeypatch.setattr(Pipeline, "_local_model_ready", lambda self: True)
    monkeypatch.setattr(p.transcriber, "transcribe", fake_local)
    from qool.transcriber import LoadInfo
    monkeypatch.setattr(Pipeline, "load", lambda self, force_cpu=False: LoadInfo("cpu", "int8", 1.0, "turbo"))
    p.transcriber.info = LoadInfo("cpu", "int8", 1.0, "turbo")
    with patch.object(groq_stt, "transcribe", side_effect=groq_stt.GroqQuotaError("خلص الحد")):
        out = p.process_audio(AUDIO)
    assert local[0], "لازم يكمّل على الجهاز"
    assert out.info["device"] == "cpu"
    assert "تعذّر التفريغ أونلاين" in out.info["fallback"]


def test_pipeline_raises_when_fallback_disabled():
    s = _settings(groq_fallback_local=False)
    p = Pipeline(s)
    with patch.object(groq_stt, "transcribe", side_effect=groq_stt.GroqQuotaError("خلص الحد")):
        with pytest.raises(groq_stt.GroqQuotaError):
            p.process_audio(AUDIO)


def test_backend_local_ignores_groq_key():
    s = _settings(stt_backend="local")
    assert Pipeline(s).uses_groq() is False


def test_groq_backend_without_key_stays_local():
    s = Settings(stt_backend="groq")
    assert Pipeline(s).uses_groq() is False


# ---- التحقق من شكل المفتاح قبل أي طلب ----

@pytest.mark.parametrize("bad,hint", [
    ("بسم الله الرحمن الرحيم", "حروف عربية"),
    ("", "مفيش مفتاح"),
    ("gsk_ abc def", "مسافات"),
    ("sk-not-a-groq-key-123456", "gsk_"),
    ("gsk_short", "gsk_"),
])
def test_bad_keys_are_rejected_with_arabic_hint(bad, hint):
    assert hint in groq_stt.key_problem(bad)


def test_good_key_passes_validation():
    assert groq_stt.key_problem("gsk_" + "a" * 48) == ""


def test_normalize_strips_direction_marks_and_spaces():
    pasted = "‏ gsk_" + "b" * 48 + "‎\n"
    assert groq_stt.normalize_key(pasted) == "gsk_" + "b" * 48


def test_dictated_arabic_never_reaches_the_network():
    """الإملاء لو وقع في خانة المفتاح: لازم رسالة واضحة، مش انهيار UnicodeEncodeError."""
    with patch("urllib.request.urlopen", lambda *a, **k: pytest.fail("ما كانش المفروض يتصل")):
        with pytest.raises(groq_stt.GroqAuthError, match="حروف عربية"):
            groq_stt.transcribe(AUDIO, api_key="بسم الله الرحمن الرحيم")
        with pytest.raises(groq_stt.GroqAuthError, match="حروف عربية"):
            groq_stt.check_key("بسم الله الرحمن الرحيم")


def test_groq_error_is_kept_when_there_is_no_local_model(monkeypatch):
    """في الوضع الأونلاين مفيش نموذج منزَّل، فلازم نقول سبب فشل الأونلاين
    مش «النموذج غير منزَّل»، وإلا المستخدم يضيع ومعاه التسجيل."""
    s = _settings()
    p = Pipeline(s)
    monkeypatch.setattr(Pipeline, "_local_model_ready", lambda self: False)
    with patch.object(groq_stt, "transcribe", side_effect=groq_stt.GroqQuotaError("خلص الحد المجاني")):
        with pytest.raises(groq_stt.GroqQuotaError, match="الحد المجاني"):
            p.process_audio(AUDIO)
