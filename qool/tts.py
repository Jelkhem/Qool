"""نطق النص بأصوات Edge النيورال (مجانية، بدون مفتاح) وفكّها إلى PCM جاهز للتشغيل."""
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
