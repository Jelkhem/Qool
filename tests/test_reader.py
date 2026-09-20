import json
import threading
from io import BytesIO

import pytest

from qool import llm
from qool.reader import speakable, take_sentences
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


def test_resolve_model_prefers_newest_flash(monkeypatch):
    monkeypatch.setattr(llm, "list_models",
                        lambda key: ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-2.5-pro"])
    assert llm.resolve_model("k", llm.AUTO) == "gemini-3.5-flash"


def test_resolve_model_keeps_explicit_choice():
    assert llm.resolve_model("k", "gemini-2.5-pro") == "gemini-2.5-pro"
