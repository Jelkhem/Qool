"""الإعدادات المحلية المحفوظة في settings.json."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .paths import settings_file

log = logging.getLogger(__name__)

DEFAULT_HOTKEY = "Ctrl+Alt+Space"
FALLBACK_HOTKEY = "Ctrl+Alt+Q"
DEFAULT_PROMPT = "ده كلام باللهجة المصرية، وممكن يكون فيه كلمات إنجليزي زي Claude وExcel وAPI."


@dataclass
class Settings:
    hotkey: str = DEFAULT_HOTKEY
    cancel_hotkey: str = "Ctrl+Alt+Backspace"
    auto_paste: bool = True
    review_mode: bool = False          # ينسخ فقط دون لصق
    microphone: str = ""               # اسم الجهاز؛ فارغ = الافتراضي
    model: str = "large-v3-turbo"
    custom_model_path: str = ""
    device: str = "auto"               # auto | cuda | cpu
    gpu_compute_type: str = "int8_float16"
    cpu_compute_type: str = "int8"
    beam_size: int = 5
    keep_model_loaded: bool = True
    unload_after_minutes: int = 15     # 0 = لا يُحرَّر أبدًا
    preload_on_start: bool = True
    initial_prompt: str = DEFAULT_PROMPT
    vocabulary: str = "Claude, Excel, API, Word, PowerPoint, Teams, Outlook"
    cleanup_enabled: bool = True
    paragraph_gap_seconds: float = 3.0  # 0 = بلا فقرات
    max_recording_minutes: int = 15
    show_overlay: bool = True
    start_with_windows: bool = False
    bridge_hotkey: str = "Ctrl+Alt+M"  # تشغيل/إيقاف جسر الميتينج
    bridge_microphone: str = ""        # فارغ = مايك Windows الافتراضي (غير الكابل)
    bridge_app: str = "ms-teams.exe"
    bridge_ai_app: str = "ChatGPT.exe"  # لما يتكلم على نفس جهاز Teams بنكتم صوت الميتينج عنه
    theme: str = "system"              # system | dark | light
    first_run_done: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_file()
        s = cls()
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                known = {f.name: f for f in fields(cls)}
                for k, v in raw.items():
                    if k in known and isinstance(v, type(getattr(s, k))):
                        setattr(s, k, v)
                    elif k in known and isinstance(getattr(s, k), float) and isinstance(v, int):
                        setattr(s, k, float(v))
        except Exception:
            log.exception("تعذر قراءة الإعدادات؛ سيتم استخدام الافتراضي")
        return s

    def save(self, path: Path | None = None) -> None:
        path = path or settings_file()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
