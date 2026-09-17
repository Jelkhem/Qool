"""مسارات البيانات المحلية (خارج OneDrive)."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    # داخل مجلد التطبيق وليس %LOCALAPPDATA%: تطبيق Claude (MSIX) يحوّل الكتابة في AppData
    # إلى مجلد افتراضي خاص به، فتختفي الإعدادات والنماذج عند التشغيل من سطح المكتب.
    base = os.environ.get("QOOL_DATA_DIR") or Path(__file__).resolve().parent.parent / "data"
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def models_dir() -> Path:
    p = data_dir() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_file() -> Path:
    return data_dir() / "settings.json"
