"""اختصارات عامة عبر RegisterHotKey في خيط مستقل له حلقة رسائل Win32."""
from __future__ import annotations

import ctypes
import logging
import queue
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_USER = 0x0312, 0x0400
WM_APP_CMD = WM_USER + 77
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "backspace": 0x08, "tab": 0x09, "esc": 0x1B,
    "escape": 0x1B, "pause": 0x13, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "`": 0xC0, "scrolllock": 0x91,
}
_KEYS.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
_KEYS.update({str(d): 0x30 + d for d in range(10)})
_KEYS.update({f"f{n}": 0x6F + n for n in range(1, 25)})
_KEYS.update({f"num{d}": 0x60 + d for d in range(10)})
_NO_MOD_OK = {f"f{n}" for n in range(1, 25)} | {"pause", "scrolllock"}


class HotkeyError(ValueError):
    pass


def parse_hotkey(spec: str) -> tuple[int, int]:
    """«Ctrl+Alt+Space» -> (modifiers, vk). يرفض المفاتيح العادية دون معدِّل."""
    parts = [p.strip().lower() for p in (spec or "").replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise HotkeyError("الاختصار فارغ")
    mods, key = 0, None
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        elif p in _KEYS and key is None:
            key = p
        else:
            raise HotkeyError(f"مفتاح غير معروف أو مكرر: {p}")
    if key is None:
        raise HotkeyError("يجب أن يحتوي الاختصار على مفتاح غير المعدِّلات")
    if mods == 0 and key not in _NO_MOD_OK:
        raise HotkeyError("استخدم Ctrl أو Alt أو Shift مع المفتاح حتى لا يُحجز مفتاح عادي")
    return mods, _KEYS[key]


def normalize_hotkey(spec: str) -> str:
    mods, vk = parse_hotkey(spec)
    names = [n for m, n in ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"), (MOD_WIN, "Win")) if mods & m]
    key = next(k for k, v in _KEYS.items() if v == vk and k not in ("return", "escape"))
    names.append(key.upper() if len(key) == 1 else key.capitalize() if not key.startswith("f") else key.upper())
    return "+".join(names)


class HotkeyManager:
    """يدير الاختصارات في خيط واحد (RegisterHotKey مرتبط بالخيط الذي سجّل)."""

    def __init__(self, on_hotkey: Callable[[str], None]):
        self._on_hotkey = on_hotkey
        self._cmds: "queue.Queue[tuple]" = queue.Queue()
        self._thread_id = 0
        self._ready = threading.Event()
        self._ids: dict[int, str] = {}
        self._next_id = 1
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def _run(self):
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # إنشاء طابور الرسائل
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                name = self._ids.get(msg.wParam)
                if name:
                    try:
                        self._on_hotkey(name)
                    except Exception:
                        log.exception("خطأ في معالجة الاختصار")
            elif msg.message == WM_APP_CMD:
                self._drain(user32)
        for hid in list(self._ids):
            user32.UnregisterHotKey(None, hid)

    def _drain(self, user32):
        while True:
            try:
                cmd, args, result, done = self._cmds.get_nowait()
            except queue.Empty:
                return
            try:
                if cmd == "register":
                    name, spec = args
                    for hid, n in list(self._ids.items()):
                        if n == name:
                            user32.UnregisterHotKey(None, hid)
                            del self._ids[hid]
                    mods, vk = parse_hotkey(spec)
                    hid = self._next_id
                    self._next_id += 1
                    ctypes.windll.kernel32.SetLastError(0)
                    ok = user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk)
                    err = ctypes.GetLastError()
                    if ok:
                        self._ids[hid] = name
                    result.append((bool(ok), err))
                elif cmd == "unregister":
                    (name,) = args
                    for hid, n in list(self._ids.items()):
                        if n == name:
                            user32.UnregisterHotKey(None, hid)
                            del self._ids[hid]
                    result.append((True, 0))
            except HotkeyError as e:
                result.append((False, str(e)))
            except Exception as e:  # pragma: no cover
                log.exception("فشل أمر الاختصار")
                result.append((False, str(e)))
            finally:
                done.set()

    def _call(self, cmd, *args):
        result, done = [], threading.Event()
        self._cmds.put((cmd, args, result, done))
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_APP_CMD, 0, 0)
        if not done.wait(5):
            return False, "انتهت مهلة تسجيل الاختصار"
        return result[0]

    def register(self, name: str, spec: str) -> tuple[bool, str]:
        """يرجع (نجاح، رسالة خطأ مفهومة)."""
        ok, err = self._call("register", name, spec)
        if ok:
            return True, ""
        if err == ERROR_HOTKEY_ALREADY_REGISTERED:
            return False, f"الاختصار {spec} مستخدم بالفعل من برنامج آخر. اختر اختصارًا مختلفًا."
        if isinstance(err, str):
            return False, err
        return False, f"تعذر تسجيل الاختصار {spec} (رمز الخطأ {err})."

    def unregister(self, name: str) -> None:
        self._call("unregister", name)

    def stop(self):
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
            self._thread.join(2)
