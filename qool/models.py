"""كتالوج نماذج faster-whisper (CTranslate2) وتنزيلها بتقدم فعلي، مرة واحدة فقط."""
from __future__ import annotations

import json
import logging
import shutil
import threading
import urllib.request
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


def _list_files(repo: str) -> list[tuple[str, int]]:
    url = f"https://huggingface.co/api/models/{repo}/tree/main"
    with urllib.request.urlopen(url, timeout=30) as r:
        items = json.load(r)
    return [(i["path"], int(i.get("size", 0))) for i in items
            if i.get("type") == "file" and not i["path"].startswith(".") and i["path"] != "README.md"]


def download(key: str, progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None) -> Path:
    """ينزّل النموذج إلى مجلد النماذج المحلي مع الاستكمال عند الانقطاع."""
    info = CATALOG[key]
    dest = model_path(key)
    dest.mkdir(parents=True, exist_ok=True)
    files = _list_files(info.repo)
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
        req = urllib.request.Request(f"https://huggingface.co/{info.repo}/resolve/main/{name}")
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
    log.info("اكتمل تنزيل النموذج %s", key)
    return dest


def delete_model(key: str) -> None:
    p = model_path(key)
    if p.exists():
        shutil.rmtree(p)
