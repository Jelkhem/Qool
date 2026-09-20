"""تشفير مفاتيح الـAPI بحساب ويندوز الحالي (DPAPI) بدل حفظها نصًا صريحًا."""
from __future__ import annotations

import base64
import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)

_DPAPI = "dpapi:"
_PLAIN = "plain:"


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


try:
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB), ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB), ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
except OSError:  # pragma: no cover - ويندوز فقط
    _crypt32 = _kernel32 = None


def _in_blob(data: bytes) -> tuple[_BLOB, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _take(out: _BLOB) -> bytes:
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        if out.pbData:
            _kernel32.LocalFree(out.pbData)


def protect(text: str) -> str:
    """يرجّع نصًا جاهزًا للحفظ في settings.json. مشفَّر لو DPAPI متاح."""
    if not text:
        return ""
    if _crypt32 is not None:
        blob_in, _keep = _in_blob(text.encode("utf-8"))
        out = _BLOB()
        if _crypt32.CryptProtectData(ctypes.byref(blob_in), "Qool API key", None, None, None, 0,
                                     ctypes.byref(out)):
            return _DPAPI + base64.b64encode(_take(out)).decode("ascii")
        log.warning("تعذر تشفير المفتاح بـ DPAPI (%s)؛ سيُحفظ كما هو", ctypes.get_last_error())
    return _PLAIN + text


def unprotect(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith(_PLAIN):
        return stored[len(_PLAIN):]
    if stored.startswith(_DPAPI):
        if _crypt32 is None:
            return ""
        try:
            blob_in, _keep = _in_blob(base64.b64decode(stored[len(_DPAPI):]))
        except Exception:
            return ""
        out = _BLOB()
        if _crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0,
                                       ctypes.byref(out)):
            return _take(out).decode("utf-8", "replace")
        log.warning("تعذر فك تشفير المفتاح؛ أعد إدخاله من الإعدادات")
        return ""
    return stored  # مفاتيح قديمة اتحفظت قبل التشفير


def mask(key: str) -> str:
    """عرض آمن في الواجهة: أول ٤ وآخر ٤ حروف فقط."""
    if not key:
        return ""
    return key[:4] + "…" + key[-4:] if len(key) > 12 else "…" * 4
