import json
import threading
from io import BytesIO

import pytest

import numpy as np

from qool import llm, tts
from qool.reader import coalesce, speakable, take_sentences
from qool.secrets import mask, protect, unprotect


# ---------- تنظيف النص قبل النطق ----------
def test_markdown_symbols_removed():
    out = speakable("## عنوان\n- نقطة **مهمة** فيها `كود`\n")
    assert "#" not in out and "*" not in out and "`" not in out
    assert "عنوان" in out and "نقطة مهمة فيها كود" in out


def test_code_block_becomes_one_word():
    out = speakable("قبل\n```python\nprint('x')\nfor i in range(9):\n    pass\n```\nبعد")
    assert "print" not in out and "range" not in out
    assert "(كود)" in out and "قبل" in out and "بعد" in out


def test_table_rows_stay_on_separate_lines():
    out = speakable("| Step | Time |\n|------|------|\n| build | 3 min |\n| test | 1 min |")
    assert out.splitlines() == ["Step ، Time", "build ، 3 min", "test ، 1 min"]


def test_link_keeps_label_and_drops_url():
    out = speakable("شوف [التوثيق](https://example.com/a/b) كده")
    assert "التوثيق" in out and "example.com" not in out


def test_bare_url_replaced():
    assert "example.com" not in speakable("افتح https://example.com/x دلوقتي")


# ---------- تقطيع الجُمَل ----------
def test_sentence_emitted_only_when_complete():
    sents, rest = take_sentences("دي جملة طويلة كفاية عشان تتقال لوحدها")
    assert sents == [] and rest  # مفيش نقطة لسه


def test_stream_splits_on_sentence_end():
    buf = ""
    got = []
    for chunk in ["الأول لازم تشغّل الأمر ده كله. ", "وبعدين تفتح الملف وتغيّر السطر. ", "خلاص كده."]:
        buf += chunk
        sents, buf = take_sentences(buf)
        got += sents
    sents, buf = take_sentences(buf, flush=True)
    got += sents
    assert len(got) >= 2
    assert "".join(got).count("الأول لازم") == 1
    assert buf == ""


def test_decimal_number_not_split():
    sents, _ = take_sentences("الحجم بقى 3.5 جيجا بعد الضغط وده كويس جدًا.", flush=True)
    assert sents == ["الحجم بقى 3.5 جيجا بعد الضغط وده كويس جدًا."]


def test_long_run_without_punctuation_is_chunked():
    long_text = "كلمة " * 200
    sents, rest = take_sentences(long_text, flush=True)
    assert len(sents) > 1
    assert all(len(s) <= 400 for s in sents)


def test_flush_returns_everything():
    sents, rest = take_sentences("جملة من غير نقطة في الآخر", flush=True)
    assert sents == ["جملة من غير نقطة في الآخر"] and rest == ""


# ---------- تخزين المفتاح ----------
def test_api_key_roundtrip_and_not_plaintext():
    key = "AIzaSyFAKEKEYFORTESTS1234567890"
    stored = protect(key)
    assert key not in stored
    assert unprotect(stored) == key


def test_unprotect_tolerates_legacy_plaintext():
    assert unprotect("AIzaSyOLD") == "AIzaSyOLD"
    assert unprotect("") == ""


def test_mask_hides_middle():
    assert mask("AIzaSy1234567890") == "AIza…7890"


# ---------- تدفق Gemini ----------
def _sse(chunks):
    body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
    return BytesIO(body.encode("utf-8"))


def _text_chunk(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_stream_yields_text_pieces(monkeypatch):
    monkeypatch.setattr(llm, "_request",
                        lambda *a, **k: _sse([_text_chunk("أهلا "), _text_chunk("بيك.")]))
    out = list(llm.stream_explain("hi", api_key="k", model="m"))
    assert "".join(out) == "أهلا بيك."


def test_stream_skips_thought_parts(monkeypatch):
    chunks = [{"candidates": [{"content": {"parts": [
        {"text": "تفكير داخلي", "thought": True}, {"text": "الرد."}]}}]}]
    monkeypatch.setattr(llm, "_request", lambda *a, **k: _sse(chunks))
    assert "".join(llm.stream_explain("hi", api_key="k", model="m")) == "الرد."


def test_stream_stops_on_cancel(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(llm, "_request", lambda *a, **k: _sse([_text_chunk("مش المفروض يظهر")]))
    assert list(llm.stream_explain("hi", api_key="k", model="m", cancel=cancel)) == []


def test_blocked_prompt_raises(monkeypatch):
    monkeypatch.setattr(llm, "_request",
                        lambda *a, **k: _sse([{"promptFeedback": {"blockReason": "SAFETY"}}]))
    with pytest.raises(llm.LLMError):
        list(llm.stream_explain("hi", api_key="k", model="m"))


def test_missing_key_raises_before_network():
    with pytest.raises(llm.LLMError):
        list(llm.stream_explain("hi", api_key="", model="m"))


def test_resolve_model_follows_measured_preference(monkeypatch):
    # الترتيب بالاستجابة المقاسة مش بالأحدث: 2.5-flash أسبق من 3.5-flash في GEMINI_PREFERRED
    monkeypatch.setattr(llm, "list_models",
                        lambda key: ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-pro"])
    assert llm.resolve_model("k", llm.AUTO) == "gemini-2.5-flash"


def test_fallback_models_skips_the_ones_already_tried(monkeypatch):
    monkeypatch.setattr(llm, "list_models",
                        lambda key: ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-3.5-flash"])
    out = llm.fallback_models("k", {"gemini-3.6-flash"})
    assert "gemini-3.6-flash" not in out
    assert out[0] == "gemini-2.5-flash"


def test_trailing_error_detects_mid_stream_503():
    # Gemini بيقطع التدفق ويكتب الخطأ في جسم الرد بدل ما يرجّع رمز HTTP
    lines = ["{", '"error": {', '"code": 503,', '"message": "high demand",', '"status": "UNAVAILABLE"', "}", "}"]
    err = llm._trailing_error(lines)
    assert isinstance(err, llm.Overloaded)


def test_trailing_error_ignores_clean_stream():
    assert llm._trailing_error([]) is None


def test_resolve_model_keeps_explicit_choice():
    assert llm.resolve_model("k", "gemini-2.5-pro") == "gemini-2.5-pro"


# ---------- صوت Gemini ----------
def _audio_sse(chunks_b64):
    body = "".join(
        "data: " + json.dumps(
            {"candidates": [{"content": {"parts": [{"inlineData": {"data": b}}]}}]}) + "\n\n"
        for b in chunks_b64)
    return BytesIO(body.encode("utf-8"))


def _b64_silence(samples):
    import base64
    return base64.b64encode(np.zeros(samples, dtype="<i2").tobytes()).decode()


def test_stream_gemini_yields_float_audio(monkeypatch):
    monkeypatch.setattr(tts, "_gemini_request",
                        lambda *a, **k: _audio_sse([_b64_silence(480), _b64_silence(480)]))
    parts = list(tts.stream_gemini("أهلا", "Kore", "key"))
    assert len(parts) == 2
    assert all(p.dtype == np.float32 for p in parts)
    assert sum(p.size for p in parts) == 960


def test_stream_gemini_raises_when_no_audio(monkeypatch):
    monkeypatch.setattr(tts, "_gemini_request", lambda *a, **k: BytesIO(b"data: {}\n\n"))
    with pytest.raises(tts.TTSError):
        list(tts.stream_gemini("أهلا", "Kore", "key"))


def test_stream_gemini_surfaces_mid_stream_overload(monkeypatch):
    body = ("data: " + json.dumps(
        {"candidates": [{"content": {"parts": [{"inlineData": {"data": _b64_silence(240)}}]}}]})
        + '\n\n{\n"error": {\n"code": 503,\n"message": "busy"\n}\n}\n')
    monkeypatch.setattr(tts, "_gemini_request", lambda *a, **k: BytesIO(body.encode("utf-8")))
    with pytest.raises(llm.Overloaded):
        list(tts.stream_gemini("أهلا", "Kore", "key"))


def test_stream_gemini_needs_a_key():
    with pytest.raises(tts.TTSError):
        list(tts.stream_gemini("أهلا", "Kore", ""))


# ---------- تجميع أجزاء الصوت ----------
def test_coalesce_merges_tiny_chunks():
    tiny = [np.zeros(960, dtype=np.float32)] * 30      # ٤٠ مللي × ٣٠ = ١.٢ ثانية
    out = list(coalesce(tiny, seconds=0.5))
    assert len(out) < len(tiny)
    assert sum(p.size for p in out) == 30 * 960
    assert out[0].size >= int(0.5 * tts.RATE)


def test_coalesce_keeps_the_tail():
    out = list(coalesce([np.zeros(100, dtype=np.float32)], seconds=0.5))
    assert len(out) == 1 and out[0].size == 100


def test_coalesce_skips_empty_pieces():
    pieces = [np.zeros(0, dtype=np.float32), np.zeros(240, dtype=np.float32)]
    assert sum(p.size for p in coalesce(pieces, seconds=0.5)) == 240


# ---------- تدوير موديلات الصوت وحصة اليوم ----------
def test_rotation_moves_on_when_a_model_hits_its_daily_limit(monkeypatch):
    calls, dead = [], []

    def fake_stream(text, voice, api_key, model, style, cancel):
        calls.append(model)
        if model == "model-a":
            raise llm.QuotaError("خلص")
        yield np.zeros(240, dtype=np.float32)

    monkeypatch.setattr(tts, "_stream_one", fake_stream)
    out = list(tts.stream_gemini("أهلا", "Kore", "key", models=["model-a", "model-b"],
                                 on_exhausted=dead.append))
    assert calls == ["model-a", "model-b"]
    assert dead == ["model-a"]          # الأول اتسجّل خلصان، التاني اشتغل
    assert sum(p.size for p in out) == 240


def test_rotation_raises_when_every_model_is_done(monkeypatch):
    def fake_stream(text, voice, api_key, model, style, cancel):
        raise llm.QuotaError("خلص")
        yield  # pragma: no cover

    monkeypatch.setattr(tts, "_stream_one", fake_stream)
    with pytest.raises(llm.QuotaError):
        list(tts.stream_gemini("أهلا", "Kore", "key", models=["a", "b"]))


def test_rotation_with_no_models_left_is_a_quota_error():
    with pytest.raises(llm.QuotaError):
        list(tts.stream_gemini("أهلا", "Kore", "key", models=[]))


def test_dead_models_are_skipped_then_reset_next_day(tmp_path):
    from qool.config import Settings

    s = Settings()
    path = tmp_path / "settings.json"
    s.save(path)
    all_models = ["a", "b"]
    assert s.live_voice_models(all_models) == ["a", "b"]
    s.note_voice_model_dead("a")
    assert s.live_voice_models(all_models) == ["b"]
    s.tts_day = "2000-01-01"            # يوم جديد
    assert s.live_voice_models(all_models) == ["a", "b"]
    assert s.tts_calls == 0 and not s.tts_exhausted


def test_discover_models_falls_back_when_offline(monkeypatch):
    import urllib.error

    monkeypatch.setattr(tts, "_tts_models_cache", None)

    def boom(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert tts.discover_models("key") == list(tts.GEMINI_TTS_MODELS)
