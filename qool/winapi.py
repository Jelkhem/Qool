"""واجهات Win32: النافذة المستهدفة، الحافظة Unicode، إرسال Ctrl+V، كشف حقول كلمة المرور."""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

HWND, HANDLE = wintypes.HWND, wintypes.HANDLE

user32.GetForegroundWindow.restype = HWND
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindow.argtypes = [HWND]
user32.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetAncestor.argtypes = [HWND, wintypes.UINT]
user32.GetAncestor.restype = HWND
user32.OpenClipboard.argtypes = [HWND]
user32.SetClipboardData.argtypes = [wintypes.UINT, HANDLE]
user32.SetClipboardData.restype = HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = HANDLE
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = HWND
user32.DestroyWindow.argtypes = [HWND]
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = HANDLE
kernel32.GlobalLock.argtypes = [HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [HANDLE]
kernel32.GlobalFree.argtypes = [HANDLE]
kernel32.GlobalFree.restype = HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = HANDLE
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
advapi32.OpenProcessToken.argtypes = [HANDLE, wintypes.DWORD, ctypes.POINTER(HANDLE)]
advapi32.GetTokenInformation.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20
GWL_STYLE = -16
ES_PASSWORD = 0x0020
GA_ROOT = 2
HWND_MESSAGE = HWND(-3)


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", HWND),
                ("hwndFocus", HWND), ("hwndCapture", HWND), ("hwndMenuOwner", HWND),
                ("hwndMoveSize", HWND), ("hwndCaret", HWND), ("rcCaret", wintypes.RECT)]


user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    focus_hwnd: int
    pid: int
    process_name: str
    class_name: str
    title_hash: int = 0  # بصمة العنوان فقط (لا يُحفظ ولا يُسجَّل العنوان نفسه)

    def describe(self) -> str:
        return self.process_name or "نافذة غير معروفة"


def _hwnd_int(h) -> int:
    return int(h or 0)


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _process_name(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def capture_foreground() -> WindowTarget | None:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    focus = 0
    if tid and user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        focus = _hwnd_int(info.hwndFocus)
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return WindowTarget(_hwnd_int(hwnd), focus, pid.value, _process_name(pid.value), _class_name(hwnd),
                        hash(buf.value))


def same_target(saved: WindowTarget | None, current: WindowTarget | None) -> bool:
    """نفس النافذة ونفس العملية، ونفس عنصر التركيز إن أمكن معرفته."""
    if saved is None or current is None:
        return False
    if saved.hwnd != current.hwnd or saved.pid != current.pid:
        return False
    if saved.focus_hwnd and current.focus_hwnd and saved.focus_hwnd != current.focus_hwnd:
        return False
    # تغيّر العنوان غالبًا يعني تبويبًا أو محادثة أخرى في نفس النافذة
    if saved.title_hash and current.title_hash and saved.title_hash != current.title_hash:
        return False
    return True


def window_exists(hwnd: int) -> bool:
    return bool(user32.IsWindow(HWND(hwnd)))


def is_process_elevated(pid: int) -> bool | None:
    """True/False، أو None إذا تعذر المعرفة (غالبًا عملية بصلاحيات أعلى)."""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        tok = HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return None
        try:
            val = wintypes.DWORD()
            ret = wintypes.DWORD()
            if not advapi32.GetTokenInformation(tok, TokenElevation, ctypes.byref(val), ctypes.sizeof(val), ctypes.byref(ret)):
                return None
            return bool(val.value)
        finally:
            kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(h)


def current_process_elevated() -> bool:
    return bool(is_process_elevated(os.getpid()))


# ---------- كشف حقل كلمة المرور ----------

def _win32_password(focus_hwnd: int) -> bool:
    if not focus_hwnd:
        return False
    cls = _class_name(HWND(focus_hwnd)).lower()
    style = user32.GetWindowLongW(HWND(focus_hwnd), GWL_STYLE)
    return "edit" in cls and bool(style & ES_PASSWORD)


def _uia_password(result: list) -> None:
    try:
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        try:
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uia  # type: ignore

            auto = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
            el = auto.GetFocusedElement()
            result.append(bool(el.CurrentIsPassword) if el else False)
        finally:
            comtypes.CoUninitialize()
    except Exception:
        log.debug("UIA غير متاح لكشف كلمة المرور", exc_info=True)


def is_password_field(target: WindowTarget, timeout: float = 1.0) -> bool | None:
    """True إذا كان التركيز على حقل كلمة مرور قابل للكشف؛ None إذا تعذر الفحص."""
    if _win32_password(target.focus_hwnd):
        return True
    result: list = []
    t = threading.Thread(target=_uia_password, args=(result,), daemon=True, name="uia-check")
    t.start()
    t.join(timeout)
    return result[0] if result else None


# ---------- الحافظة ----------

class ClipboardError(RuntimeError):
    pass


def _open_clipboard(owner, attempts: int, delay: float) -> bool:
    for i in range(attempts):
        if user32.OpenClipboard(owner):
            return True
        time.sleep(delay * (i + 1))
    return False


def set_clipboard_text(text: str, attempts: int = 8, delay: float = 0.03) -> None:
    """ينسخ نصًا Unicode. يرفع ClipboardError بعد محاولات محدودة."""
    owner = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None)
    try:
        if not _open_clipboard(owner, attempts, delay):
            raise ClipboardError("الحافظة مشغولة ببرنامج آخر")
        try:
            if not user32.EmptyClipboard():
                raise ClipboardError("تعذر تفريغ الحافظة")
            data = (text + "\0").encode("utf-16-le")
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                raise ClipboardError("تعذر حجز ذاكرة للحافظة")
            p = kernel32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(CF_UNICODETEXT, h):
                kernel32.GlobalFree(h)
                raise ClipboardError("تعذر وضع النص في الحافظة")
        finally:
            user32.CloseClipboard()
    finally:
        if owner:
            user32.DestroyWindow(owner)


def get_clipboard_text(attempts: int = 8, delay: float = 0.03) -> str | None:
    if not _open_clipboard(None, attempts, delay):
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


# ---------- لوحة المفاتيح ----------

VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_V = 0x10, 0x11, 0x12, 0x5B, 0x5C, 0x56
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
QOOL_SIGNATURE = 0x51004F4F


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def modifiers_down() -> bool:
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))


def wait_modifiers_released(timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not modifiers_down():
            return True
        time.sleep(0.02)
    return not modifiers_down()


def send_ctrl_v() -> tuple[bool, int]:
    """يرسل Ctrl+V مرة واحدة. يرجع (نجاح إدراج كل الأحداث، رمز الخطأ)."""
    seq = [(VK_CONTROL, 0), (VK_V, 0), (VK_V, KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP)]
    arr = (INPUT * len(seq))()
    for i, (vk, flags) in enumerate(seq):
        arr[i].type = INPUT_KEYBOARD
        arr[i].u.ki = KEYBDINPUT(vk, 0, flags, 0, QOOL_SIGNATURE)
    n = user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
    return n == len(seq), (ctypes.get_last_error() if n != len(seq) else 0)
