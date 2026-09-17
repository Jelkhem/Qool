"""التشغيل مع Windows عبر اختصار في مجلد Startup الخاص بالمستخدم (بدون صلاحيات مسؤول)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

SHORTCUT_NAME = "Qool.lnk"


def _startup_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def create_shortcut(path: Path, args: str = "") -> None:
    import comtypes.client

    root = project_root()
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    sc = shell.CreateShortcut(str(path))
    sc.TargetPath = str(pythonw if pythonw.exists() else Path(sys.executable).with_name("pythonw.exe"))
    sc.Arguments = f"-m qool {args}".strip()
    sc.WorkingDirectory = str(root)
    sc.Description = "قول — إملاء صوتي محلي"
    sc.Save()


def set_startup(enabled: bool) -> None:
    path = _startup_dir() / SHORTCUT_NAME
    if enabled:
        create_shortcut(path)
    elif path.exists():
        path.unlink()


def is_startup_enabled() -> bool:
    return (_startup_dir() / SHORTCUT_NAME).exists()
