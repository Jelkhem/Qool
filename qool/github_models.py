"""تنزيل النماذج من GitHub Releases — مصدر بديل لما Hugging Face يكون محجوبًا.

الملف الكبير متقسّم لأجزاء (حد GitHub ٢ جيجا للملف)، فبنلمّها هنا ونتأكد من
بصمة SHA-256 المكتوبة في models.json قبل ما نعتبر النموذج جاهزًا.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

REPO = "Jelkhem/Qool"
TAG = "models-v1"
BASE = f"https://github.com/{REPO}/releases/download/{TAG}"
MANIFEST_URL = f"{BASE}/models.json"
USER_AGENT = "Qool/1.0"
_CHUNK = 1 << 20


def _open(url: str, offset: int = 0, timeout: float = 60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if offset:
        req.add_header("Range", f"bytes={offset}-")
    return urllib.request.urlopen(req, timeout=timeout)


def manifest(timeout: float = 30) -> dict:
    with _open(MANIFEST_URL, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def available(timeout: float = 30) -> list[str]:
    """أسماء النماذج المتاحة على GitHub، أو قائمة فاضية لو مش واصل."""
    try:
        return list(manifest(timeout))
    except Exception as e:
        log.info("تعذّر قراءة قائمة نماذج GitHub: %s", e)
        return []


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _fetch(name: str, dest: Path, expect: dict, done: list[int], total: int,
           progress: Callable[[int, int], None] | None, cancel: threading.Event | None) -> Path:
    """ينزّل أصلًا واحدًا مع الاستكمال، ويتحقق من حجمه وبصمته."""
    if dest.exists() and dest.stat().st_size == expect["size"] and _sha256(dest) == expect["sha256"]:
        done[0] += expect["size"]
        if progress:
            progress(done[0], total)
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    with _open(f"{BASE}/{name}", offset=have) as r:
        if have and r.status != 206:
            have = 0
        done[0] += have
        with open(part, "ab" if have else "wb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    from .models import DownloadCancelled
                    raise DownloadCancelled()
                block = r.read(_CHUNK)
                if not block:
                    break
                f.write(block)
                done[0] += len(block)
                if progress:
                    progress(done[0], total)
    if part.stat().st_size != expect["size"]:
        raise IOError(f"حجم {name} مش مطابق؛ أعد المحاولة")
    if _sha256(part) != expect["sha256"]:
        part.unlink(missing_ok=True)
        raise IOError(f"بصمة {name} مش مطابقة؛ الملف وصل ناقص أو متغيّر")
    part.replace(dest)
    return dest


def download(key: str, dest_dir: Path, progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None) -> Path:
    """ينزّل نموذجًا كاملًا من الإصدار ويجمّعه في dest_dir."""
    info = manifest()[key]
    dest_dir.mkdir(parents=True, exist_ok=True)
    staging = dest_dir / ".parts"
    staging.mkdir(exist_ok=True)
    total = sum(p["size"] for p in info["parts"]) + info["extras"]["size"]
    done = [0]
    parts = [_fetch(p["name"], staging / p["name"], p, done, total, progress, cancel)
             for p in info["parts"]]
    extras = _fetch(info["extras"]["name"], staging / info["extras"]["name"],
                    info["extras"], done, total, progress, cancel)

    model_bin = dest_dir / "model.bin"
    with open(model_bin, "wb") as out:          # لمّ الأجزاء بالترتيب
        for part in parts:
            with open(part, "rb") as f:
                shutil.copyfileobj(f, out, _CHUNK)
    with zipfile.ZipFile(extras) as z:
        z.extractall(dest_dir)
    shutil.rmtree(staging, ignore_errors=True)
    log.info("تم تنزيل النموذج %s من GitHub (%.0f MB)", key, total / 2**20)
    return dest_dir
