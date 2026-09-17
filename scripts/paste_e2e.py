"""اختبار لصق حقيقي على سطح المكتب داخل محرر تجريبي (بيانات تجريبية فقط، لا يرسل أي شيء).

يغيّر نافذة المقدمة مؤقتًا — لا تكتب في برامج أخرى أثناء تشغيله (حوالي 15 ثانية).
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qool import winapi  # noqa: E402
from qool.paster import Outcome, Paster  # noqa: E402

TEXT = "نص تجريبي: ما تبعتش العرض قبل ما أراجع السعر — Claude وExcel وAPI بسعر 1,250 جنيه."
tmp = Path(tempfile.mkdtemp(prefix="qool_e2e_"))
procs = []
results = []


def _click_window_of(pid: int) -> bool:
    """أداة اختبار فقط: نقرة ماوس حقيقية على المحرر (مسموحة من Windows) لجعله في المقدمة."""
    u = ctypes.windll.user32
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        p = ctypes.c_ulong()
        u.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(ctypes.c_void_p(hwnd)):
            found.append(hwnd)
        return True

    u.EnumWindows(WNDENUMPROC(cb), 0)
    if not found:
        return False

    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    r = RECT()
    u.GetWindowRect(ctypes.c_void_p(found[0]), ctypes.byref(r))
    old = POINT()
    u.GetCursorPos(ctypes.byref(old))
    u.SetCursorPos((r.l + r.r) // 2, (r.t + r.b) // 2 + 20)
    u.mouse_event(0x0002, 0, 0, 0, 0)  # left down
    u.mouse_event(0x0004, 0, 0, 0, 0)  # left up
    time.sleep(0.2)
    u.SetCursorPos(old.x, old.y)
    return True


def editor(name):
    out = tmp / f"{name}.json"
    p = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "test_editor.py"), str(out), f"QoolTest-{name}"])
    procs.append(p)
    end = time.time() + 12
    clicked = False
    while time.time() < end:
        # python.exe داخل venv مُشغِّل وسيط؛ النافذة تتبع عملية ابن
        import psutil
        try:
            pids = {p.pid} | {c.pid for c in psutil.Process(p.pid).children(recursive=True)}
        except psutil.NoSuchProcess:
            pids = {p.pid}
        fg = winapi.capture_foreground()
        if fg and fg.pid in pids:
            time.sleep(0.4)
            return p, out, winapi.capture_foreground()
        if not clicked and out.exists():
            time.sleep(0.5)
            clicked = any(_click_window_of(pid) for pid in pids if pid != p.pid)
        time.sleep(0.1)
    return p, out, None


def content(out):
    try:
        return json.loads(out.read_text(encoding="utf-8"))
    except Exception:
        return {"text": None, "enters": None}


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("✓ " if cond else "✗ ") + name + (f" — {detail}" if detail else ""))


paster = Paster()
try:
    pa, out_a, target_a = editor("A")
    if target_a is None:
        print("تعذر أن يصبح المحرر التجريبي في المقدمة (قيود Windows على سرقة التركيز). يلزم اختبار يدوي.")
        sys.exit(2)

    # 1) نفس الهدف: لصقة واحدة، بلا Enter
    d = paster.deliver(TEXT, target_a, True)
    time.sleep(0.8)
    c = content(out_a)
    check("لصق في النافذة المستهدفة", d.outcome == Outcome.PASTED and c["text"] == TEXT, d.outcome.value)
    check("لصقة واحدة فقط (لا تكرار)", c["text"] is not None and c["text"].count("نص تجريبي") == 1)
    check("لم يُضغط Enter ولم يُضف سطر", c["enters"] == 0 and "\n" not in (c["text"] or ""))
    check("الحافظة تحتفظ بالنص Unicode", winapi.get_clipboard_text() == TEXT)

    # 2) تغيّر النافذة قبل انتهاء التفريغ: لا لصق في مكان خاطئ
    pb, out_b, target_b = editor("B")
    before_a = content(out_a)["text"]
    d = paster.deliver("نص يجب ألا يُلصق", target_a, True)
    time.sleep(0.8)
    check("تغيّر الهدف يمنع اللصق", d.outcome == Outcome.TARGET_CHANGED
          and content(out_b)["text"] == "" and content(out_a)["text"] == before_a, d.outcome.value)
    check("النص محفوظ في الحافظة بعد منع اللصق", winapi.get_clipboard_text() == "نص يجب ألا يُلصق")

    # 3) وضع المراجعة: نسخ دون لصق
    d = paster.deliver("مراجعة فقط", target_b, False)
    time.sleep(0.6)
    check("وضع المراجعة ينسخ ولا يلصق", d.outcome == Outcome.COPIED_ONLY and content(out_b)["text"] == "", d.outcome.value)

    # 4) الحافظة مشغولة ببرنامج آخر
    held, release = threading.Event(), threading.Event()

    def hold():
        u = ctypes.windll.user32
        if u.OpenClipboard(None):
            held.set()
            release.wait(4)
            u.CloseClipboard()

    th = threading.Thread(target=hold)
    th.start()
    held.wait(2)
    d = paster.deliver("نص والحافظة مشغولة", target_b, True)
    release.set(); th.join()
    time.sleep(0.6)
    check("فشل الحافظة يُبلَّغ ولا يُعلَن نجاح ولا لصق", d.outcome == Outcome.CLIPBOARD_FAILED
          and content(out_b)["text"] == "", d.message)
finally:
    for p in procs:
        p.terminate()
    time.sleep(0.3)

ok = all(r for _, r in results)
print(f"\nالنتيجة: {sum(r for _, r in results)}/{len(results)} نجحت")
sys.exit(0 if ok else 1)
