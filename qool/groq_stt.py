"""تفريغ أونلاين عبر Groq (نفس موديلات Whisper لكن على سيرفرهم).

يُستعمل كبديل اختياري للتفريغ المحلي على الأجهزة الضعيفة أو بدون كارت NVIDIA.
الصوت يُرسل إلى سيرفر Groq، فالقرار بخصوص الخصوصية متروك للمستخدم في الإعدادات.
"""
from __future__ import annotations

import io
import json
import logging
import time
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np

from .cleanup import Segment

log = logging.getLogger(__name__)

HOST = "https://api.groq.com/openai/v1"
# Cloudflare أمام Groq بيرفض الطلب (403 code 1010) لو الـUser-Agent هو urllib الافتراضي
USER_AGENT = "Qool/1.0 (+https://github.com/Jelkhem/Qool)"
MODELS = ("whisper-large-v3-turbo", "whisper-large-v3")
DEFAULT_MODEL = "whisper-large-v3-turbo"
MAX_UPLOAD_MB = 24  # حد Groq للطبقة المجانية 25 MB؛ نترك هامشًا


class GroqError(RuntimeError):
    """خطأ يمكن عرضه للمستخدم كما هو."""


class GroqAuthError(GroqError):
    pass


class GroqQuotaError(GroqError):
    pass


def wav_bytes(audio: np.ndarray, rate: int = 16000) -> bytes:
    """يحوّل الصوت (float32 -1..1) إلى WAV 16-bit في الذاكرة — لا يُكتب أي ملف على القرص."""
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _multipart(fields: dict[str, str], wav: bytes) -> tuple[bytes, str]:
    boundary = f"----qool{uuid.uuid4().hex}"
    out = bytearray()
    for name, value in fields.items():
        if value == "":
            continue
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode()
        out += value.encode("utf-8") + b"\r\n"
    out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n".encode()
    out += b"Content-Type: audio/wav\r\n\r\n" + wav + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _error(e: urllib.error.HTTPError) -> GroqError:
    try:
        detail = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
    except Exception:
        detail = ""
    if e.code in (401, 403):
        return GroqAuthError("مفتاح Groq مرفوض. راجع المفتاح في الإعدادات." + (f" ({detail})" if detail else ""))
    if e.code == 413:
        return GroqError("التسجيل أطول من حد الرفع. سجّل مقطعًا أقصر أو استخدم التفريغ المحلي.")
    if e.code == 429:
        return GroqQuotaError("خلص الحد المجاني لدلوقتي على Groq. استنى شوية أو استخدم التفريغ المحلي.")
    if e.code >= 500:
        return GroqError(f"سيرفر Groq مش مستجيب دلوقتي (رمز {e.code}).")
    return GroqError(detail or f"Groq رفض الطلب (رمز {e.code}).")


def transcribe(audio: np.ndarray, *, api_key: str, model: str = DEFAULT_MODEL, language: str = "ar",
               prompt: str = "", rate: int = 16000, timeout: float = 120.0) -> tuple[list[Segment], float]:
    """(المقاطع، زمن المعالجة). يرفع الصوت إلى Groq ويعيد النص مقسّمًا بالتوقيتات."""
    if not api_key:
        raise GroqAuthError("مفيش مفتاح Groq محفوظ. أضفه من الإعدادات ← النموذج.")
    wav = wav_bytes(audio, rate)
    size_mb = len(wav) / 2**20
    if size_mb > MAX_UPLOAD_MB:
        raise GroqError(f"حجم الصوت {size_mb:.0f} MB أكبر من حد الرفع ({MAX_UPLOAD_MB} MB). "
                        "سجّل مقطعًا أقصر أو استخدم التفريغ المحلي.")
    body, content_type = _multipart(
        {"model": model, "language": language, "prompt": prompt[:800],
         "response_format": "verbose_json", "temperature": "0"}, wav)
    req = urllib.request.Request(f"{HOST}/audio/transcriptions", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", USER_AGENT)
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise _error(e) from None
    except urllib.error.URLError as e:
        raise GroqError(f"تعذّر الوصول للإنترنت ({e.reason}).") from None
    took = time.perf_counter() - t
    segments = [Segment(s.get("text", "").strip(), float(s.get("start", 0.0)), float(s.get("end", 0.0)))
                for s in data.get("segments") or [] if s.get("text", "").strip()]
    if not segments:
        text = (data.get("text") or "").strip()
        segments = [Segment(text, 0.0, float(len(audio) / rate))] if text else []
    log.info("تفريغ Groq: صوت=%.1fث رفع=%.1fMB زمن=%.2fث موديل=%s", len(audio) / rate, size_mb, took, model)
    return segments, took


def check_key(api_key: str, timeout: float = 20.0) -> list[str]:
    """يتحقق من المفتاح ويعيد أسماء موديلات التفريغ المتاحة له."""
    req = urllib.request.Request(f"{HOST}/models")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise _error(e) from None
    except urllib.error.URLError as e:
        raise GroqError(f"تعذّر الوصول للإنترنت ({e.reason}).") from None
    return sorted(m["id"] for m in data.get("data", []) if "whisper" in m.get("id", ""))
