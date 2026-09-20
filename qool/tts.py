"""نطق النص: صوت Gemini (بينطق العامية المصرية صح) وأصوات Edge المجانية كاحتياطي."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import threading

import numpy as np

log = logging.getLogger(__name__)

RATE = 24000  # أصوات Edge كلها 24 كيلوهرتز مونو

VOICES = (
    ("ar-EG-SalmaNeural", "سلمى — مصري، صوت حريمي (الافتراضي)"),
    ("ar-EG-ShakirNeural", "شاكر — مصري، صوت رجالي"),
    ("ar-SA-ZariyahNeural", "زارية — فصحى، صوت حريمي"),
    ("ar-SA-HamedNeural", "حامد — فصحى، صوت رجالي"),
    ("en-US-AriaNeural", "Aria — إنجليزي أمريكي"),
    ("en-US-GuyNeural", "Guy — إنجليزي أمريكي، رجالي"),
)
DEFAULT_VOICE = VOICES[0][0]

# --- صوت Gemini: موديل لغوي، بينطق العامية المصرية صح ويقبل توجيه بالكلام ---
# الحد المجاني ١٠ طلبات في اليوم **لكل موديل**، فالتدوير بينهم بيزوّد عدد القراءات.
# القائمة دي احتياطي بس؛ الأصل إننا نكتشفها من الـAPI عشان أي موديل جديد يتحسب لوحده.
GEMINI_TTS_MODELS = ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts",
                     "gemini-2.5-pro-preview-tts")
GEMINI_TTS_MODEL = GEMINI_TTS_MODELS[0]
_tts_models_cache: list[str] | None = None


def _tts_rank(name: str) -> tuple[int, str]:
    """flash الأول (أسرع وأرخص)، وبعدين الباقي."""
    return (0 if "flash" in name else 1), name


def discover_models(api_key: str, refresh: bool = False) -> list[str]:
    """كل موديلات الصوت المتاحة للمفتاح ده، مرتّبة. لو الاكتشاف فشل بنرجّع القائمة الثابتة."""
    global _tts_models_cache
    with _lock:
        if _tts_models_cache is not None and not refresh:
            return list(_tts_models_cache)
    import json
    import urllib.error
    import urllib.request

    from . import llm

    try:
        req = urllib.request.Request(f"{llm.GEMINI_HOST}/models?pageSize=300")
        req.add_header("x-goog-api-key", api_key)
        with urllib.request.urlopen(req, timeout=20) as r:
            payload = json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("تعذّر اكتشاف موديلات الصوت (%s)؛ هنستخدم القائمة الثابتة", e)
        return list(GEMINI_TTS_MODELS)
    found = [
        name for m in payload.get("models", [])
        if "tts" in (name := m.get("name", "").removeprefix("models/")).lower()
        and "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    found.sort(key=_tts_rank)
    if not found:
        return list(GEMINI_TTS_MODELS)
    with _lock:
        _tts_models_cache = found
    log.info("موديلات الصوت المتاحة: %s", ", ".join(found))
    return list(found)
GEMINI_VOICES = (
    ("Kore", "Kore — حريمي، واضح (الافتراضي)"),
    ("Sulafat", "Sulafat — حريمي، دافئ"),
    ("Achernar", "Achernar — حريمي، هادئ"),
    ("Zephyr", "Zephyr — حريمي، مشرق"),
    ("Charon", "Charon — رجالي"),
    ("Puck", "Puck — رجالي، حيوي"),
)
DEFAULT_GEMINI_VOICE = GEMINI_VOICES[0][0]
DEFAULT_STYLE = ("اقرأ النص ده بلهجة مصرية عامية طبيعية، بنبرة زميل بيشرح لزميله بهدوء ووضوح. "
                 "متزوّدش أي كلام من عندك ومتعلّقش على النص.")
# موديلات 2.5 بترفض التوجيه بالعربي («بيحاول يولّد نص») وبتقبل الصيغة الإنجليزية دي.
STYLE_25 = "Say in a natural, calm Egyptian Arabic accent, reading exactly this text:"


def style_for(model: str, style: str) -> str:
    return STYLE_25 if model.startswith("gemini-2.5") else style

_voices_cache: list[tuple[str, str]] | None = None
_lock = threading.Lock()


class TTSError(RuntimeError):
    pass


def _proxy() -> str | None:
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or None


def rate_str(percent: int) -> str:
    """-50..+100 -> «+10%» بالشكل اللي edge-tts بيفهمه."""
    percent = max(-50, min(100, int(percent)))
    return f"{percent:+d}%"


async def _collect(text: str, voice: str, rate: str) -> bytes:
    import edge_tts

    buf = bytearray()
    comm = edge_tts.Communicate(text, voice, rate=rate, proxy=_proxy())
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf += chunk["data"]
    return bytes(buf)


def _decode(mp3: bytes) -> np.ndarray:
    import av

    with av.open(io.BytesIO(mp3), format="mp3") as cont:
        stream = cont.streams.audio[0]
        res = av.AudioResampler(format="flt", layout="mono", rate=RATE)
        parts = []
        for frame in cont.decode(stream):
            for out in res.resample(frame):
                parts.append(out.to_ndarray().reshape(-1))
        for out in res.resample(None):  # تفريغ ما تبقّى
            parts.append(out.to_ndarray().reshape(-1))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32, copy=False)


def synth(text: str, voice: str = DEFAULT_VOICE, rate_percent: int = 0) -> np.ndarray:
    """يحوّل جملة إلى موجة float32 مونو 24 كيلوهرتز. يرفع TTSError عند الفشل."""
    text = (text or "").strip()
    if not text or not any(ch.isalnum() for ch in text):
        return np.zeros(0, dtype=np.float32)
    try:
        mp3 = asyncio.run(_collect(text, voice or DEFAULT_VOICE, rate_str(rate_percent)))
    except Exception as e:  # شبكة، أو صوت غير معروف، أو رفض من الخدمة
        raise TTSError(f"تعذّر توليد الصوت ({type(e).__name__}: {e}).") from None
    if not mp3:
        raise TTSError("الخدمة رجّعت صوت فاضي.")
    try:
        return _decode(mp3)
    except Exception as e:
        raise TTSError(f"تعذّر فكّ ترميز الصوت ({e}).") from None


def _gemini_request(text: str, voice: str, api_key: str, model: str, style: str, stream: bool):
    import json
    import urllib.error
    import urllib.request

    from . import llm

    if not api_key:
        raise TTSError("مفيش مفتاح Gemini، فمش هينفع صوت Gemini.")
    body = {
        "contents": [{"parts": [{"text": f"{style}\n\n{text}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {
                "voiceName": voice or DEFAULT_GEMINI_VOICE}}},
        },
    }
    body["contents"][0]["parts"][0]["text"] = f"{style_for(model, style)}\n\n{text}"
    verb = "streamGenerateContent?alt=sse" if stream else "generateContent"
    url = f"{llm.GEMINI_HOST}/models/{model or GEMINI_TTS_MODEL}:{verb}"
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 method="POST")
    req.add_header("x-goog-api-key", api_key)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        return urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        raise llm.gemini_error(e) from None
    except urllib.error.URLError as e:
        raise TTSError(f"تعذّر الوصول للإنترنت ({e.reason}).") from None


def _audio_data(payload: dict) -> str | None:
    try:
        return payload["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    except (KeyError, IndexError, TypeError):
        return None


def _to_float(b64: str) -> np.ndarray:
    import base64

    return np.frombuffer(base64.b64decode(b64), dtype="<i2").astype(np.float32) / 32768.0


def stream_gemini(text: str, voice: str, api_key: str, model: str = "",
                  style: str = DEFAULT_STYLE, cancel=None, models: list[str] | None = None,
                  on_exhausted=None):
    """نطق بموديل Gemini الصوتي، بالتدفق: أول جزء صوت بيوصل خلال ~١.٢ ثانية
    بدل ما نستنى الجملة كلها تتولّد (~٩ ثواني).

    الحد المجاني ١٠ طلبات في اليوم **لكل موديل**، فلما واحد يخلص بنعدّي على اللي بعده
    وبنبلّغ `on_exhausted(model)` عشان ما نضيّعش وقت في تجربته تاني النهاردة.
    """
    from . import llm

    text = (text or "").strip()
    if not text or not any(ch.isalnum() for ch in text):
        return
    if model:
        queue = [model]
    elif models is None:
        queue = list(GEMINI_TTS_MODELS)
    else:
        queue = list(models)      # قائمة فاضية = خلص كل حاجة، مش «استخدم الافتراضي»
    if not queue:
        raise llm.QuotaError("خلصت حصة كل موديلات الصوت النهاردة.")
    last: Exception | None = None
    for i, m in enumerate(queue):
        try:
            yield from _stream_one(text, voice, api_key, m, style, cancel)
            return
        except llm.QuotaError as e:
            last = e
            if on_exhausted is not None:
                on_exhausted(m)
            if i == len(queue) - 1:
                raise
            log.info("حصة %s خلصت؛ بنجرّب %s", m, queue[i + 1])
    if last is not None:
        raise last


def _stream_one(text: str, voice: str, api_key: str, model: str, style: str, cancel):
    import json

    resp = _gemini_request(text, voice, api_key, model, style, stream=True)
    other = []
    got = False
    with resp:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                return
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                if line:
                    other.append(line)
                continue
            try:
                chunk = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            data = _audio_data(chunk)
            if data:
                got = True
                yield _to_float(data)
    from . import llm

    err = llm._trailing_error(other)
    if err is not None:
        raise err
    if not got:
        raise TTSError("Gemini رجّع رد من غير صوت.")


def synth_gemini(text: str, voice: str, api_key: str, model: str = "",
                 style: str = DEFAULT_STYLE) -> np.ndarray:
    """نفس الحاجة لكن دفعة واحدة — للاختبارات والمعاينة القصيرة."""
    parts = list(stream_gemini(text, voice, api_key, model, style))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def list_voices(refresh: bool = False) -> list[tuple[str, str]]:
    """كل الأصوات المتاحة من الخدمة؛ لو النت مقطوع بترجع القائمة المختصرة."""
    global _voices_cache
    with _lock:
        if _voices_cache is not None and not refresh:
            return _voices_cache
    try:
        import edge_tts

        async def _go():
            return await edge_tts.list_voices(proxy=_proxy())

        raw = asyncio.run(_go())
    except Exception as e:
        log.warning("تعذّر جلب قائمة الأصوات: %s", e)
        return list(VOICES)
    arabic, other = [], []
    for v in raw:
        short, locale = v.get("ShortName", ""), v.get("Locale", "")
        if not short:
            continue
        label = f"{short}  ({locale})"
        (arabic if locale.startswith("ar-") else other).append((short, label))
    merged = list(VOICES) + [v for v in arabic + other if v[0] not in {x[0] for x in VOICES}]
    with _lock:
        _voices_cache = merged
    return merged
