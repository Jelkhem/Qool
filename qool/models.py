"""كتالوج نماذج faster-whisper (CTranslate2) وتنزيلها بتقدم فعلي، مرة واحدة فقط."""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import models_dir

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelInfo:
    key: str
    repo: str
    size_mb: int
    label: str
    vram_mb_int8_fp16: int  # تقدير يُستخدم فقط لقرار «هل تكفي ذاكرة الكارت»


CATALOG: dict[str, ModelInfo] = {
    m.key: m
    for m in (
        ModelInfo("small", "Systran/faster-whisper-small", 464, "small — خفيف وسريع، دقة أقل", 700),
        ModelInfo("medium", "Systran/faster-whisper-medium", 1460, "medium — متوسط", 1300),
        ModelInfo("large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", 1547,
                  "large-v3-turbo — موصى به (دقة جيدة وسرعة عالية)", 1500),
        ModelInfo("large-v3", "Systran/faster-whisper-large-v3", 2948, "large-v3 — الأدق والأبطأ", 2600),
    )
}

REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")
_COMPLETE_MARK = ".complete"

DEFAULT_ENDPOINT = "https://huggingface.co"
# مرايا تُجرَّب تلقائيًا لو الأصل محجوب.
# ملاحظة مقاسة (٢٥ أكتوبر ٢٠٢٦): hf-mirror.com بيرد 308 ويحوّل لـ huggingface.co نفسه،
# فهو مش حل للحجب — سايبينه لأنه بيفيد لو الحجب على مستوى DNS فقط، والحل الأكيد
# للأجهزة المحجوبة هو استيراد النموذج من فلاشة.
MIRRORS = ("https://hf-mirror.com",)


def endpoint() -> str:
    """مصدر النماذج: متغيّر بيئة أو الإعداد، وإلا Hugging Face."""
    return (os.environ.get("QOOL_MODEL_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")


def blocked_message(e: Exception, host: str = "") -> str:
    """رسالة عربية مفهومة بدل نص urllib الإنجليزي."""
    host = host or endpoint()
    detail = str(e)[:90]
    return (f"تعذّر الوصول لمصدر النماذج {host} — غالبًا محجوب من شبكة الشركة.\n"
            "جرّبنا المرايا البديلة كمان وما نفعتش.\n"
            "الحل: استورد النموذج من مجلد أو فلاشة (زر «استيراد نموذج…»)، "
            "أو اطلب من IT السماح بـ huggingface.co و cdn-lfs.hf.co.\n"
            f"تفاصيل فنية: {detail}")


def import_model(source: str | Path, key: str | None = None) -> str:
    """ينسخ نموذجًا جاهزًا من مجلد أو ملف ZIP (فلاشة مثلًا) — بدون إنترنت.

    يرجّع اسم النموذج اللي اتسطّب.
    """
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError("المسار مش موجود.")
    if src.is_file():
        if src.suffix.lower() != ".zip":
            raise ValueError("الملف لازم يكون مجلد نموذج أو ملف ZIP.")
        tmp = models_dir() / f".import-{src.stem}"
        if tmp.exists():
            shutil.rmtree(tmp)
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        inner = [d for d in tmp.rglob("*") if d.is_dir() and is_valid_model_dir(d)]
        folder = inner[0] if inner else tmp
    else:
        folder = src
        tmp = None
    if not is_valid_model_dir(folder):
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        raise ValueError("المجلد ده مش نموذج صالح (لازم يحتوي model.bin و config.json و tokenizer.json).")
    name = key or folder.name
    dest = model_path(name)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(folder, dest)
    (dest / _COMPLETE_MARK).write_text("ok", encoding="utf-8")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    log.info("تم استيراد النموذج %s من %s", name, src)
    return name


def model_path(key: str) -> Path:
    return models_dir() / key


def is_downloaded(key: str) -> bool:
    p = model_path(key)
    return (p / _COMPLETE_MARK).exists() and all((p / f).exists() for f in REQUIRED_FILES)


def is_valid_model_dir(path: str | Path) -> bool:
    p = Path(path)
    return p.is_dir() and all((p / f).exists() for f in REQUIRED_FILES)


class DownloadCancelled(Exception):
    pass


def sources(allow_mirrors: bool = True) -> list[str]:
    """مصادر التنزيل بالترتيب. لو المستخدم حدّد مصدرًا صراحةً فهو الوحيد."""
    if os.environ.get("QOOL_MODEL_ENDPOINT"):
        return [endpoint()]
    return [DEFAULT_ENDPOINT, *MIRRORS] if allow_mirrors else [DEFAULT_ENDPOINT]


def _list_files(repo: str, base: str | None = None) -> list[tuple[str, int]]:
    url = f"{(base or endpoint()).rstrip('/')}/api/models/{repo}/tree/main"
    with urllib.request.urlopen(url, timeout=30) as r:
        items = json.load(r)
    return [(i["path"], int(i.get("size", 0))) for i in items
            if i.get("type") == "file" and not i["path"].startswith(".") and i["path"] != "README.md"]


def download(key: str, progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None, allow_mirrors: bool = True) -> Path:
    """ينزّل النموذج مع الاستكمال عند الانقطاع.

    لو المصدر الأساسي محجوب، بيجرّب المرايا المعروفة لوحده قبل ما يفشل.
    """
    last: Exception | None = None
    for base in sources(allow_mirrors):
        try:
            return _download_from(base, key, progress, cancel)
        except DownloadCancelled:
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            log.warning("فشل التنزيل من %s: %s", base, e)
            last = e
    raise last if last else RuntimeError("مفيش مصدر تنزيل متاح")


def _download_from(base: str, key: str, progress, cancel) -> Path:
    info = CATALOG[key]
    dest = model_path(key)
    dest.mkdir(parents=True, exist_ok=True)
    files = _list_files(info.repo, base)
    if base != DEFAULT_ENDPOINT:
        log.info("التنزيل من مصدر بديل: %s", base)
    total = sum(s for _, s in files)
    done = 0
    for name, size in files:
        target = dest / name
        if target.exists() and target.stat().st_size == size:
            done += size
            if progress:
                progress(done, total)
            continue
        part = dest / (name + ".part")
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(f"{base.rstrip('/')}/{info.repo}/resolve/main/{name}")
        if have:
            req.add_header("Range", f"bytes={have}-")
        with urllib.request.urlopen(req, timeout=60) as r:
            if have and r.status != 206:
                have = 0
            mode = "ab" if have else "wb"
            done += have
            with open(part, mode) as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled()
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        if size and part.stat().st_size != size:
            raise IOError(f"حجم الملف {name} غير مطابق؛ أعد المحاولة")
        part.replace(target)
    (dest / _COMPLETE_MARK).write_text("ok", encoding="utf-8")
    log.info("اكتمل تنزيل النموذج %s من %s", key, base)
    return dest


def delete_model(key: str) -> None:
    p = model_path(key)
    if p.exists():
        shutil.rmtree(p)
