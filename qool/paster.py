"""تسليم النص: نسخ Unicode ثم لصقة واحدة فقط في النافذة المستهدفة بعد التحقق منها."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger(__name__)


class Outcome(str, Enum):
    PASTED = "pasted"
    COPIED_ONLY = "copied_only"
    TARGET_CHANGED = "target_changed"
    NO_TARGET = "no_target"
    PASSWORD_FIELD = "password_field"
    ELEVATED = "elevated"
    MODIFIERS_HELD = "modifiers_held"
    CLIPBOARD_FAILED = "clipboard_failed"
    SEND_FAILED = "send_failed"


@dataclass
class Delivery:
    outcome: Outcome
    message: str

    @property
    def in_clipboard(self) -> bool:
        return self.outcome != Outcome.CLIPBOARD_FAILED


class Paster:
    def __init__(self, win=None):
        if win is None:
            from . import winapi as win
        self.win = win

    def deliver(self, text: str, target, auto_paste: bool, should_abort=lambda: False) -> Delivery:
        w = self.win
        if should_abort():
            return Delivery(Outcome.TARGET_CHANGED, "تم الإلغاء؛ لم يتم النسخ أو اللصق.")
        try:
            w.set_clipboard_text(text)
        except Exception as e:
            log.warning("فشل النسخ إلى الحافظة: %s", e)
            return Delivery(Outcome.CLIPBOARD_FAILED,
                            f"تعذر النسخ إلى الحافظة ({e}). النص محفوظ في نافذة التطبيق — اضغط «نسخ» للمحاولة مجددًا.")
        if not auto_paste:
            return Delivery(Outcome.COPIED_ONLY, "النص في الحافظة وجاهز للصق (Ctrl+V).")
        if target is None:
            return Delivery(Outcome.NO_TARGET, "النص جاهز للنسخ — لم تُحدَّد خانة كتابة عند بدء الإملاء.")
        current = w.capture_foreground()
        if not w.same_target(target, current):
            return Delivery(Outcome.TARGET_CHANGED,
                            "النص جاهز للنسخ — تغيّرت النافذة منذ بدء الإملاء، فلم يتم اللصق تجنبًا للصق في مكان خاطئ.")
        if w.is_password_field(current) is True:
            return Delivery(Outcome.PASSWORD_FIELD, "النص جاهز للنسخ — الخانة الحالية حقل كلمة مرور، فلم يتم اللصق.")
        if w.is_process_elevated(target.pid) and not w.current_process_elevated():
            return Delivery(Outcome.ELEVATED,
                            "النص جاهز للنسخ — البرنامج المستهدف يعمل بصلاحيات مسؤول ولا يسمح Windows باللصق فيه تلقائيًا.")
        if not w.wait_modifiers_released(2.0):
            return Delivery(Outcome.MODIFIERS_HELD, "النص جاهز للنسخ — ما زالت مفاتيح Ctrl/Alt/Shift مضغوطة، فلم يتم اللصق.")
        if should_abort():
            return Delivery(Outcome.COPIED_ONLY, "تم الإلغاء قبل اللصق؛ النص في الحافظة فقط.")
        # إعادة التحقق مباشرة قبل الإرسال (قد يتغير التركيز أثناء الانتظار)
        if not w.same_target(target, w.capture_foreground()):
            return Delivery(Outcome.TARGET_CHANGED, "النص جاهز للنسخ — تغيّرت النافذة قبل اللصق مباشرة.")
        ok, err = w.send_ctrl_v()
        if not ok:
            return Delivery(Outcome.SEND_FAILED,
                            f"النص جاهز للنسخ — رفض Windows إرسال أمر اللصق (رمز {err}). الصق يدويًا بـ Ctrl+V.")
        name = target.describe() if hasattr(target, "describe") else ""
        return Delivery(Outcome.PASTED, f"أُرسل أمر اللصق إلى {name}. راجع النص قبل الإرسال.")
