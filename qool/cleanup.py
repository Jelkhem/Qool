"""تنظيف محافظ للنص المفرَّغ.

المبدأ: لا نغيّر الكلمات ولا اللهجة ولا النفي ولا الأرقام. نسمح فقط بـ:
- ضبط المسافات وعلامات الترقيم.
- حذف أصوات التردد الصريحة (اممم، um، uh).
- حذف التلعثم المعلَّم صراحة (مثل «ع- عايز»).
- حذف تكرار حروف الجر/الربط المتلاصق («في في»)، وليس تكرار كلمات المعنى («لا لا»، «جدا جدا»).
- حذف عبارات هلوسة معروفة لا يقولها المستخدم أبدًا («ترجمة نانسي قنقر»).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

ARABIC_LETTER = r"ء-يٱ-ۓۺ-ۿ"
_AR = re.compile(f"[{ARABIC_LETTER}]")

# عبارات تظهر في نماذج Whisper نتيجة بيانات تدريب الترجمة، وليست كلامًا حقيقيًا.
HALLUCINATION_PHRASES = [
    "ترجمة نانسي قنقر",
    "نانسي قنقر",
    "Subtitles by the Amara.org community",
]

# أصوات تردد صريحة فقط. لا نحذف «آه» (نعم) ولا «يعني» ولا «بص» ولا «أمم» (جمع أمة).
_HESITATION = re.compile(
    r"^(?:[اإأ]?م{3,}|م{2,}|ا{3,}|إ{2,}|ه{3,}م*|u+h+m*|u+m+|h+m+|m{2,}|e+r+m+|e+h+m*)$",
    re.IGNORECASE,
)

# كلمات وظيفية يكون تكرارها المتلاصق تلعثمًا في الغالب.
_FUNCTION_WORDS = {
    "في", "من", "على", "عن", "إلى", "الى", "اللي", "إن", "ان", "أن", "لما", "عشان",
    "the", "a", "an", "to", "of", "and", "in", "is", "on", "for",
}

_TRAIL_PUNCT = "،,.؟?!؛;:"


@dataclass
class Segment:
    text: str
    start: float = 0.0
    end: float = 0.0


def _strip_token_punct(tok: str) -> tuple[str, str, str]:
    m = re.match(r"^([\"'«(]*)(.*?)([\"'»).,،؟?!؛;:…\-]*)$", tok)
    if not m:
        return "", tok, ""
    return m.group(1), m.group(2), m.group(3)


def _remove_hallucinations(text: str) -> str:
    for p in HALLUCINATION_PHRASES:
        text = re.sub(re.escape(p) + r"[.،,]?", " ", text, flags=re.IGNORECASE)
    return text


def _process_tokens(text: str) -> str:
    tokens = text.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        pre, core, post = _strip_token_punct(tok)
        # 1) أصوات التردد
        if core and _HESITATION.match(core):
            # احتفظ بعلامة ترقيم نهائية مهمة إن وُجدت (مثل نهاية جملة)
            p = post.replace("-", "").replace("…", "")
            if p and out and out[-1][-1] not in _TRAIL_PUNCT:
                out[-1] = out[-1] + p[0]
            i += 1
            continue
        # 2) تلعثم معلَّم: «ع- عايز» أو «أ... أنا»
        if core and i + 1 < len(tokens) and (post.endswith("-") or "…" in post or post.endswith("..")):
            nxt = _strip_token_punct(tokens[i + 1])[1]
            if nxt.lower().startswith(core.lower()) and len(core) <= 4:
                i += 1
                continue
        # 3) تكرار كلمة وظيفية متلاصقة بلا فاصل ترقيم
        if out and not post and core.lower() in _FUNCTION_WORDS:
            prev_pre, prev_core, prev_post = _strip_token_punct(out[-1])
            if not prev_post and prev_core.lower() == core.lower():
                i += 1
                continue
        out.append(tok)
        i += 1
    return " ".join(out)


def _fix_punctuation(text: str) -> str:
    # مسافات قبل علامات الترقيم
    text = re.sub(r"\s+([،,.؟?!؛;:])", r"\1", text)
    # فاصلة/علامة استفهام لاتينية بعد حرف عربي -> عربية (لا نلمس الأرقام مثل 1,000)
    text = re.sub(f"(?<=[{ARABIC_LETTER}]),", "،", text)
    text = re.sub(f"(?<=[{ARABIC_LETTER}])\\?", "؟", text)
    text = re.sub(f"(?<=[{ARABIC_LETTER}]);", "؛", text)
    # تكرار علامة الترقيم نفسها
    text = re.sub(r"([،,؛;])\1+", r"\1", text)
    text = re.sub(r"([،,])\s*([.؟?!])", r"\2", text)
    # مسافة بعد الفاصلة العربية/علامة الاستفهام العربية إن التصق بها حرف
    text = re.sub(f"([،؟؛])(?=[{ARABIC_LETTER}A-Za-z])", r"\1 ", text)
    return text


def clean_text(text: str) -> str:
    """ينظّف نصًا واحدًا تنظيفًا محافظًا. لا يضيف كلمات ولا يحذف كلمات معنى."""
    if not text:
        return ""
    text = text.replace("‏", "").replace("‎", "")
    text = _remove_hallucinations(text)
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t ]+", " ", line).strip()
        if not line:
            continue
        line = _process_tokens(line)
        line = _fix_punctuation(line)
        line = re.sub(r"^[،,؛;.\s]+", "", line).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines)
    if text and (text[-1].isalnum() or _AR.match(text[-1])):
        text += "."
    return text


def join_segments(segments: Iterable[Segment], paragraph_gap: float = 3.0) -> str:
    """يدمج المقاطع بمسافة، ويضيف سطرًا جديدًا فقط عند سكتة طويلة بعد جملة منتهية."""
    parts: list[str] = []
    prev: Segment | None = None
    for seg in segments:
        t = seg.text.strip()
        if not t:
            continue
        if prev is not None:
            gap = seg.start - prev.end
            ends_sentence = parts[-1].rstrip()[-1:] in ".؟?!"
            parts.append("\n" if paragraph_gap > 0 and gap >= paragraph_gap and ends_sentence else " ")
        parts.append(t)
        prev = seg
    return "".join(parts)


def is_meaningless(text: str) -> bool:
    """نص فارغ أو علامات ترقيم فقط."""
    return not re.search(f"[{ARABIC_LETTER}A-Za-z0-9٠-٩]", text or "")
