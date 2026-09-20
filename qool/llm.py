"""مخ القراءة: يبعت النص لموديل سحابي ويرجّع الشرح بالمصري كتدفق نصي.

مفيش مكتبات جديدة — urllib بس، زي ما في models.py.
المزوّد الحالي: Gemini (الخطة المجانية). الواجهة مجهَّزة لإضافة مزوّدين تانيين.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Iterator

log = logging.getLogger(__name__)

GEMINI_HOST = "https://generativelanguage.googleapis.com/v1beta"

# الترتيب مقاس فعليًا على الخطة المجانية (سبتمبر ٢٠٢٦)، مش بالأحدث:
# 3.6-flash رد في ١.٤ث و 2.5-flash في ٠.٨ث، بينما 3.7-flash راجع 503 و 3.8-flash أخد ٧.٤ث.
# الموديلات الأحدث عليها ضغط أكبر على الخطة المجانية، والاستجابة أهم من فرق جودة بسيط في التلخيص.
GEMINI_PREFERRED = (
    "gemini-3.6-flash", "gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.8-flash",
    "gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-flash-latest", "gemini-3.7-flash",
)
AUTO = "auto"

EXPLAIN_PROMPT = """إنت بتشرح لزميل مصري شغّال في الهندسة والبرمجة.
هيوصلك نص — غالبًا رد من مساعد ذكاء اصطناعي أو توثيق تقني، وغالبًا بالإنجليزي.
مهمتك تقوله الزبدة بأسلوب كلام مسموع مش مكتوب.

{{DIALECT}}

القواعد:
- ابدأ على طول بالمفيد: إيه اللي المفروض يعرفه أو يعمله. من غير مقدمات زي «النص بيقول».
- اشرح المعنى والقصد، مش ترجمة حرفية. لو في خطوات، رتّبها: الأول... بعدين... وأخيرًا...
- سيب المصطلحات التقنية وأسماء الملفات والأوامر بالإنجليزي زي ما هي: API و commit و Excel.
- متقراش الكود سطر سطر؛ قول الكود بيعمل إيه في جملة واحدة.
- ممنوع أي رموز تنسيق خالص: لا نجوم ولا شرطات ولا عناوين ولا جداول ولا إيموجي. نص عادي بس.
- جمل قصيرة، وكل جملة تنتهي بنقطة، لأن الكلام ده هيتقال بصوت.
- **قصّر**: من جملتين لأربع جمل، وما تزيدش عن ست مهما كان النص طويل.
  الكلام ده هيتسمع مش هيتقرا، والطويل بيضيّع المعنى. متكررش نفس المعلومة مرتين.
- لو في تحذير أو حاجة ممكن تغلط أو محتاجة انتباه، نبّه عليها في الآخر.
- متسألش أسئلة ومتقولش إنك ذكاء اصطناعي. اتكلم على طول."""

TRANSLATE_PROMPT = """هيوصلك نص غالبًا بالإنجليزي. رجّعه بالكامل بالعربي، من غير اختصار ومن غير حذف.

{{DIALECT}}
- ترجمة بالمعنى، مش حرفية، وبأسلوب كلام مسموع.
- سيب المصطلحات التقنية وأسماء الملفات والأوامر بالإنجليزي زي ما هي.
- ممنوع أي رموز تنسيق: لا نجوم ولا شرطات ولا عناوين ولا إيموجي.
- جمل قصيرة تنتهي بنقطة.
- متزوّدش من عندك أي تعليق، ومتقدّمش مقدمة."""

# أصوات Edge العربية متدرّبة على الفصحى: العامية المكتوبة بتتنطق بقواعد الفصحى فتطلع مكسّرة.
# فلما الصوت بيبقى Edge، النص نفسه لازم يتكتب فصحى مبسّطة — نفس المعنى، إملاء ينطقه الصوت صح.
FOSHA_RULE = """اكتب بالعربية الفصحى المبسّطة، مش بالعامية المصرية.
السبب: الكلام ده هيتقرا بصوت آلي متدرّب على الفصحى، والعامية المكتوبة بتتنطق عنده غلط.
بس خليها فصحى سهلة وقريبة، مش فصحى أدبية ولا معقّدة:
جمل قصيرة، كلمات دارجة، وأسلوب مباشر زي «أولاً، شغّل هذا الأمر وانتظر حتى ينتهي»."""

MASRI_RULE = "اكتب بالعامية المصرية زي ما بتتكلم مع زميلك بالظبط."


def prompt_for(mode: str, dialect: str = "masri") -> str:
    """البرومبت حسب المطلوب وحسب اللهجة اللي الصوت هينطقها صح."""
    base = MODES.get(mode, MODES["explain"])[1]
    if not base:
        return ""
    return base.replace("{{DIALECT}}", MASRI_RULE if dialect == "masri" else FOSHA_RULE)


MODES = {
    "explain": ("لخّص واشرح", EXPLAIN_PROMPT),
    "translate": ("ترجم كامل", TRANSLATE_PROMPT),
    "raw": ("اقرا النص زي ما هو", ""),
}


_thinking_supported = True   # يتقفل تلقائيًا لو الموديل رفض الحقل


class LLMError(RuntimeError):
    pass


class QuotaError(LLMError):
    """تخطّي حد الاستخدام المجاني (429)."""


class ModelMissing(LLMError):
    """الموديل المختار مش متاح للمفتاح ده (404)."""


class Overloaded(LLMError):
    """الموديل واقع تحت ضغط (503) — يُجرَّب غيره."""


def _request(url: str, api_key: str, body: dict | None, timeout: float, stream: bool):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("x-goog-api-key", api_key)  # في الهيدر مش في الرابط
    if data:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    if stream:
        req.add_header("Accept", "text/event-stream")
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise gemini_error(e) from None
    except urllib.error.URLError as e:
        raise LLMError(f"تعذّر الوصول للإنترنت ({e.reason}).") from None


def gemini_error(e: urllib.error.HTTPError) -> LLMError:
    try:
        detail = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
    except Exception:
        detail = ""
    if e.code in (401, 403):
        return LLMError("المفتاح مرفوض. راجع مفتاح Gemini في الإعدادات." + (f" ({detail})" if detail else ""))
    if e.code == 404:
        return ModelMissing(detail or "الموديل ده مش متاح للمفتاح بتاعك.")
    if e.code == 429:
        return QuotaError("خلص الحد المجاني لدلوقتي. استنى شوية أو غيّر الموديل من الإعدادات.")
    if e.code == 503:
        return Overloaded("الموديل ده عليه ضغط دلوقتي.")
    if e.code >= 500:
        return LLMError(f"سيرفر Gemini مش مستجيب دلوقتي (رمز {e.code}). جرّب تاني.")
    return LLMError(detail or f"رفض الطلب (رمز {e.code}).")


def list_models(api_key: str, timeout: float = 20.0) -> list[str]:
    """أسماء الموديلات اللي المفتاح ده يقدر يستخدمها لتوليد نص."""
    req = urllib.request.Request(f"{GEMINI_HOST}/models?pageSize=200")
    req.add_header("x-goog-api-key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        raise gemini_error(e) from None
    except urllib.error.URLError as e:
        raise LLMError(f"تعذّر الوصول للإنترنت ({e.reason}).") from None
    out = []
    for m in payload.get("models", []):
        name = m.get("name", "").removeprefix("models/")
        if not name or "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        if any(bad in name for bad in ("embedding", "image", "tts", "live", "transcribe", "vision")):
            continue
        out.append(name)
    return out


def resolve_model(api_key: str, wanted: str) -> str:
    """يحوّل «auto» لاسم موديل حقيقي متاح للمفتاح ده."""
    if wanted and wanted != AUTO:
        return wanted
    available = set(list_models(api_key))
    for name in GEMINI_PREFERRED:
        if name in available:
            return name
    flashes = sorted(n for n in available if "flash" in n)
    if flashes:
        return flashes[0]
    if available:
        return sorted(available)[0]
    raise LLMError("مفيش أي موديل متاح للمفتاح ده.")


def fallback_models(api_key: str, avoid: set[str]) -> list[str]:
    """الموديلات اللي نجرّبها بعد ما واحد يقع، بالترتيب."""
    available = [n for n in list_models(api_key) if n not in avoid]
    ordered = [n for n in GEMINI_PREFERRED if n in available]
    ordered += sorted(n for n in available if "flash" in n and n not in ordered)
    return ordered


def _parts_text(chunk: dict) -> str:
    out = []
    for cand in chunk.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if part.get("thought"):  # تفكير داخلي، مش للقراءة
                continue
            if isinstance(part.get("text"), str):
                out.append(part["text"])
    return "".join(out)


def _blocked_reason(chunk: dict) -> str | None:
    fb = chunk.get("promptFeedback", {})
    if fb.get("blockReason"):
        return f"النص اترفض من Gemini ({fb['blockReason']})."
    for cand in chunk.get("candidates", []):
        if cand.get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            return "النص اترفض من فلتر الأمان بتاع Gemini."
    return None


def _trailing_error(lines: list[str]) -> LLMError | None:
    """Gemini بيقطع التدفق ويكتب JSON خطأ في جسم الرد؛ لازم نلاقيه بدل ما نرجّع رد ناقص."""
    if not lines:
        return None
    try:
        info = json.loads("".join(lines)).get("error", {})
    except json.JSONDecodeError:
        return None
    code, msg = info.get("code"), info.get("message", "")
    if not code:
        return None
    if code == 503:
        return Overloaded("الموديل عليه ضغط وقطع الرد في نصّه.")
    if code == 429:
        return QuotaError("خلص الحد المجاني لدلوقتي. استنى شوية أو غيّر الموديل من الإعدادات.")
    return LLMError(msg or f"الرد اتقطع (رمز {code}).")


def stream_explain(text: str, *, api_key: str, model: str, mode: str = "explain",
                   dialect: str = "masri", cancel: threading.Event | None = None,
                   timeout: float = 90.0) -> Iterator[str]:
    """يبعت النص ويرجّع الرد قطعة قطعة وهو بيتولّد."""
    if not api_key:
        raise LLMError("مفيش مفتاح Gemini. حطّه من الإعدادات.")
    global _thinking_supported
    system = prompt_for(mode, dialect)
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192},
    }
    if _thinking_supported:
        # إغلاق التفكير الداخلي يقلّل زمن أول جملة بشكل ملحوظ
        body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    url = f"{GEMINI_HOST}/models/{model}:streamGenerateContent?alt=sse"
    try:
        resp = _request(url, api_key, body, timeout, stream=True)
    except LLMError as e:
        # موديلات معيّنة مش بتقبل thinkingConfig — شيله وما تحاولش تاني في الجلسة دي.
        if (not _thinking_supported or isinstance(e, (QuotaError, ModelMissing))
                or "thinking" not in str(e).lower()):
            raise
        log.info("الموديل %s مش بيقبل thinkingConfig؛ هنبعت من غيره", model)
        _thinking_supported = False
        body["generationConfig"].pop("thinkingConfig", None)
        resp = _request(url, api_key, body, timeout, stream=True)

    other = []          # سطور مش "data:" — الخطأ بييجي فيها في نص التدفق
    with resp:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                return
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                if line:
                    other.append(line)
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            reason = _blocked_reason(chunk)
            if reason:
                raise LLMError(reason)
            piece = _parts_text(chunk)
            if piece:
                yield piece
    err = _trailing_error(other)
    if err is not None:
        raise err
