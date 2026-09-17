"""تطبيق Qt: يربط الواجهة بالمتحكم والتسجيل والنموذج والاختصارات وشريط النظام."""
from __future__ import annotations

import getpass
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_NAME, APP_TITLE, winapi
from .audio import Recorder, list_input_devices
from .config import DEFAULT_HOTKEY, FALLBACK_HOTKEY, Settings
from .controller import Controller, State
from .hotkey import HotkeyError, HotkeyManager, normalize_hotkey
from .models import CATALOG, DownloadCancelled, delete_model, download, is_downloaded
from .paster import Outcome, Paster
from .pipeline import ModelMissing, Pipeline
from .transcriber import gpu_memory_mb
from .ui import theme
from .ui.main_window import MainWindow
from .ui.overlay import Overlay
from .ui.widgets import mic_icon

log = logging.getLogger(__name__)


class QoolApp(QObject):
    sig_event = Signal(str, dict)
    sig_hotkey = Signal(str)
    sig_recorder_problem = Signal(str)
    sig_download = Signal(int, int)
    sig_download_done = Signal(bool, str)
    sig_model = Signal(str)

    def __init__(self, qapp: QApplication):
        super().__init__()
        self.qapp = qapp
        self.settings = Settings.load()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transcribe")
        self.recorder = Recorder()
        self.recorder.on_problem = self.sig_recorder_problem.emit
        self.pipeline = Pipeline(self.settings, on_load=self._on_model_loaded)
        self.paster = Paster()
        self.controller = Controller(
            recorder=self.recorder, capture_target=self._capture_target,
            process_audio=self.pipeline.process_audio, deliver=self.paster.deliver,
            submit=lambda fn: self.worker.submit(self._guard, fn), settings=self.settings,
            emit=lambda kind, **kw: self.sig_event.emit(kind, kw), same_target=winapi.same_target,
        )
        self.downloading = False
        self._download_cancel = threading.Event()
        self._tray_hint_shown = False
        self._model_idle_since = time.monotonic()
        self.bridge = None
        self._bridge_waiting = False
        self._bridge_last_search = 0.0
        self._bridge_device_shown = ""

        self.t = theme.tokens(self.settings.theme)
        qapp.setStyleSheet(theme.stylesheet(self.t))
        self._build_icons()
        self.window = MainWindow(self)
        self.window.setWindowIcon(self.icons["idle"])
        self.overlay = Overlay()
        self._build_tray()

        self.sig_event.connect(self._on_event, Qt.QueuedConnection)
        self.sig_hotkey.connect(self._on_hotkey, Qt.QueuedConnection)
        self.sig_recorder_problem.connect(self.controller.handle_recorder_problem, Qt.QueuedConnection)
        self.sig_download.connect(self.window.set_download_progress, Qt.QueuedConnection)
        self.sig_download_done.connect(self._on_download_done, Qt.QueuedConnection)
        self.sig_model.connect(self.window.set_model_status, Qt.QueuedConnection)

        # مؤقت الواجهة يعمل فقط أثناء التسجيل (لا استهلاك CPU في الخمول)
        self.level_timer = QTimer(self, interval=50, timeout=self._tick)
        self.idle_timer = QTimer(self, interval=30_000, timeout=self._check_idle_unload)
        self.idle_timer.start()
        self.bridge_timer = QTimer(self, interval=100, timeout=self._bridge_tick)

        self.hotkeys = HotkeyManager(self.sig_hotkey.emit)
        self.window.refresh_mics()
        self._register_hotkeys_on_start()
        self._startup_model()

    # ---------- بدء التشغيل ----------
    def _register_hotkeys_on_start(self):
        s = self.settings
        ok, err = self.hotkeys.register("toggle", s.hotkey)
        if not ok and s.hotkey == DEFAULT_HOTKEY:
            ok2, _ = self.hotkeys.register("toggle", FALLBACK_HOTKEY)
            if ok2:
                s.hotkey = FALLBACK_HOTKEY
                s.save()
                msg = (f"{DEFAULT_HOTKEY} مستخدم من برنامج آخر على هذا الجهاز، "
                       f"لذلك تم استخدام {FALLBACK_HOTKEY}. يمكنك تغييره من الإعدادات.")
                self.window.set_hotkey_status(msg, True)
                self.window.show_banner(msg)
                self.tray.showMessage(APP_TITLE, msg, QSystemTrayIcon.Information, 8000)
                ok, err = True, ""
        if ok:
            self.window.set_hotkey_status(f"الاختصار {s.hotkey} مسجَّل بنجاح.", True) if not self.window.hotkey_status.text() else None
        else:
            self.window.set_hotkey_status(err, False)
            self.window.show()
        if s.cancel_hotkey:
            ok_c, err_c = self.hotkeys.register("cancel", s.cancel_hotkey)
            if not ok_c:
                self.window.set_notice("تحذير: " + err_c)
        if s.bridge_hotkey:
            ok_b, err_b = self.hotkeys.register("bridge", s.bridge_hotkey)
            self.window.set_bridge_hotkey_status(
                f"اختصار الجسر {s.bridge_hotkey} مسجَّل بنجاح." if ok_b else err_b, ok_b)

    def _startup_model(self):
        s = self.settings
        if not s.custom_model_path and not is_downloaded(s.model):
            info = CATALOG.get(s.model) or CATALOG["large-v3-turbo"]
            self.window.set_model_status(f"النموذج {info.key} غير منزَّل (~{info.size_mb} MB).")
            self.window.show()
            QTimer.singleShot(300, lambda: self._offer_download(info.key))
            return
        if s.preload_on_start:
            self.load_model_now()
        else:
            self.window.set_model_status("سيتم تحميل النموذج عند أول إملاء.")
        if not s.first_run_done:
            self.window.show()
            s.first_run_done = True
            s.save()

    def _offer_download(self, key):
        info = CATALOG[key]
        if QMessageBox.question(self.window, APP_TITLE,
                                f"لتحويل الكلام إلى نص محليًا يلزم تنزيل النموذج «{key}» مرة واحدة (~{info.size_mb} MB).\n"
                                "بعد التنزيل يعمل الإملاء دون إنترنت. هل تريد التنزيل الآن؟") == QMessageBox.Yes:
            self.start_download(key)

    # ---------- شريط النظام ----------
    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.icons["idle"], self)
        self.tray.setToolTip(f"{APP_TITLE} — جاهز")
        menu = QMenu()
        menu.setLayoutDirection(Qt.RightToLeft)
        self.tray_toggle = QAction("ابدأ التسجيل", self, triggered=self.toggle)
        menu.addAction(self.tray_toggle)
        menu.addAction(QAction("إلغاء", self, triggered=self.cancel))
        menu.addSeparator()
        self.tray_bridge = QAction("تشغيل جسر الميتينج", self, triggered=self.toggle_bridge)
        menu.addAction(self.tray_bridge)
        menu.addSeparator()
        menu.addAction(QAction("إظهار النافذة", self, triggered=self.show_window))
        menu.addAction(QAction("خروج", self, triggered=self.quit))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_window() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def tray_hint_once(self):
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage(APP_TITLE, f"التطبيق ما زال يعمل في شريط النظام. الاختصار: {self.settings.hotkey}",
                                  QSystemTrayIcon.Information, 4000)

    def show_window(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    # ---------- أوامر ----------
    def _capture_target(self):
        t = winapi.capture_foreground()
        if t is None or t.pid == os.getpid():
            return None  # بدأ الإملاء من نافذة التطبيق نفسه: لا هدف للصق
        return t

    def _guard(self, fn):
        try:
            fn()
        except Exception:
            log.exception("خطأ غير متوقع في العامل")

    def _on_hotkey(self, name):
        if name == "toggle":
            self.toggle()
        elif name == "cancel":
            self.cancel()
        elif name == "bridge":
            self.toggle_bridge()

    def toggle(self):
        self.controller.toggle()

    def cancel(self):
        self.controller.cancel()

    def copy_text(self, text):
        if not text:
            return
        try:
            winapi.set_clipboard_text(text)
            self.window.set_notice("تم النسخ إلى الحافظة.")
        except Exception as e:
            self.window.set_notice(f"تعذر النسخ: {e}")

    def retry_paste(self, text):
        if self.controller.busy or not text:
            return
        threading.Thread(target=self.controller.retry_paste, args=(text,), daemon=True, name="retry-paste").start()

    def list_mics(self):
        try:
            return list_input_devices()
        except Exception as e:
            self.window.set_notice(f"تعذر قراءة أجهزة الصوت: {e}")
            return []

    def update_setting(self, key, value, reload=False):
        setattr(self.settings, key, value)
        self.settings.save()
        if reload and self.pipeline.transcriber.loaded and not self.controller.busy:
            self.worker.submit(self._guard, self.pipeline.transcriber.unload)
            self.window.set_model_status("تغيّرت إعدادات النموذج؛ سيُعاد التحميل عند الإملاء التالي.")

    def apply_hotkeys(self, toggle_spec, cancel_spec):
        try:
            toggle_spec = normalize_hotkey(toggle_spec)
            cancel_spec = normalize_hotkey(cancel_spec) if cancel_spec.strip() else ""
        except HotkeyError as e:
            self.window.set_hotkey_status(str(e), False)
            return
        if toggle_spec == cancel_spec:
            self.window.set_hotkey_status("اختصار الإلغاء يجب أن يختلف عن اختصار التسجيل.", False)
            return
        old = self.settings.hotkey
        ok, err = self.hotkeys.register("toggle", toggle_spec)
        if not ok:
            self.hotkeys.register("toggle", old)
            self.window.set_hotkey_status(err + f" (ما زال {old} فعّالًا)", False)
            return
        self.settings.hotkey = toggle_spec
        msg = f"الاختصار {toggle_spec} مسجَّل بنجاح."
        if cancel_spec:
            ok_c, err_c = self.hotkeys.register("cancel", cancel_spec)
            if ok_c:
                self.settings.cancel_hotkey = cancel_spec
            else:
                msg += " " + err_c
        else:
            self.hotkeys.unregister("cancel")
            self.settings.cancel_hotkey = ""
        self.settings.save()
        self.window.set_hotkey_status(msg, True)

    # ---------- جسر الميتينج ----------
    def apply_bridge_hotkey(self, spec):
        s = self.settings
        try:
            spec = normalize_hotkey(spec)
        except HotkeyError as e:
            self.window.set_bridge_hotkey_status(str(e), False)
            return
        if spec in (s.hotkey, s.cancel_hotkey):
            self.window.set_bridge_hotkey_status("اختصار الجسر لازم يختلف عن اختصارات الإملاء.", False)
            return
        old = s.bridge_hotkey
        ok, err = self.hotkeys.register("bridge", spec)
        if not ok:
            if old:
                self.hotkeys.register("bridge", old)
            self.window.set_bridge_hotkey_status(err + (f" (ما زال {old} فعّالًا)" if old else ""), False)
            return
        s.bridge_hotkey = spec
        s.save()
        self.window.set_bridge_hotkey_status(f"اختصار الجسر {spec} مسجَّل بنجاح.", True)

    def list_bridge_mics(self):
        from .meeting_bridge import list_bridge_mics

        return list_bridge_mics()

    def toggle_bridge(self):
        if self.bridge is not None:
            self.stop_bridge()
        else:
            self.start_bridge()

    def start_bridge(self):
        from .meeting_bridge import BridgeError, MeetingBridge

        s = self.settings
        bridge = MeetingBridge(mic_name=s.bridge_microphone, app_exe=s.bridge_app, ai_exe=s.bridge_ai_app)
        try:
            bridge.start()
        except BridgeError as e:
            self._bridge_state("error", str(e))
            return
        except Exception as e:
            log.exception("فشل تشغيل جسر الميتينج")
            self._bridge_state("error", f"تعذر تشغيل الجسر: {e}")
            return
        self.bridge = bridge
        self.bridge_timer.start()
        self._bridge_state("on", bridge.status_text())

    def stop_bridge(self, message: str = "الجسر وقف.", error: bool = False):
        bridge, self.bridge = self.bridge, None
        self.bridge_timer.stop()
        if bridge is not None:
            bridge.stop()
        self._bridge_state("error" if error else "off", message)

    def set_bridge_mic(self, name: str):
        self.update_setting("bridge_microphone", name)
        if self.bridge is not None:  # نطبّق المايك الجديد فورًا
            self.stop_bridge()
            self.start_bridge()

    def _bridge_tick(self):
        from .meeting_bridge import meter

        b = self.bridge
        if b is None:
            return
        problem = b.problem()
        if problem:
            self.stop_bridge(problem, error=True)
            return
        now = time.monotonic()
        if now - self._bridge_last_search >= 2.0:  # فحص Teams كل ثانيتين مش كل 100ms
            self._bridge_last_search = now
            if not b.app_running():
                if not self._bridge_waiting:
                    self._bridge_state("waiting", f"{b.title} مقفول — الجسر مستني يتفتح…", notify=False)
            elif self._bridge_waiting or b.device_label != self._bridge_device_shown:
                self._bridge_state("on", b.status_text(), notify=False)  # رجع أو غيّر جهاز الصوت
        self.window.set_bridge_levels(meter(b.app_ring.live_level), meter(b.mic_ring.live_level), b.ducker.active)

    def _bridge_state(self, kind: str, message: str, notify: bool = True):
        self._bridge_waiting = kind == "waiting"
        running = kind in ("on", "waiting")
        self._bridge_device_shown = self.bridge.device_label if self.bridge is not None else ""
        self.window.set_bridge_state(kind, message)
        self.tray_bridge.setText("إيقاف جسر الميتينج" if running else "تشغيل جسر الميتينج")
        if notify and self.settings.show_overlay and not self.controller.busy and kind != "waiting":
            text = {"on": "🎧 جسر الميتينج شغال", "off": "جسر الميتينج وقف"}.get(kind, "⚠ " + message[:70])
            self.overlay.show_status({"on": "done", "off": "idle"}.get(kind, "error"), text,
                                     auto_hide_ms=5000 if kind == "error" else 2500)
        if kind == "error" and not self.window.isVisible():
            self.tray.showMessage(APP_TITLE, message, QSystemTrayIcon.Warning, 6000)

    def _build_icons(self):
        t = self.t
        self.icons = {"idle": mic_icon(t["accent"]), "recording": mic_icon(t["red"]),
                      "processing": mic_icon(t["amber"]), "done": mic_icon(t["accent"], dot=t["green"]),
                      "error": mic_icon(t["accent"], dot=t["danger"])}

    def set_theme(self, mode: str):
        self.update_setting("theme", mode)
        self.t = theme.tokens(mode)
        self.qapp.setStyleSheet(theme.stylesheet(self.t))
        self._build_icons()
        self.window.setWindowIcon(self.icons["idle"])
        self.tray.setIcon(self.icons.get(self.controller.state.value, self.icons["idle"]))
        self.window.apply_theme(self.t)

    def suspend_hotkeys(self):
        """أثناء التقاط اختصار جديد في الإعدادات حتى لا يُشغَّل التسجيل بالخطأ."""
        self.hotkeys.unregister("toggle")
        self.hotkeys.unregister("cancel")
        self.hotkeys.unregister("bridge")

    def resume_hotkeys(self):
        s = self.settings
        ok, err = self.hotkeys.register("toggle", s.hotkey)
        if not ok:
            self.window.set_hotkey_status(err, False)
        if s.cancel_hotkey:
            self.hotkeys.register("cancel", s.cancel_hotkey)
        if s.bridge_hotkey:
            self.hotkeys.register("bridge", s.bridge_hotkey)

    def set_start_with_windows(self, enabled: bool):
        from .startup import set_startup

        try:
            set_startup(enabled)
            self.update_setting("start_with_windows", bool(enabled))
        except Exception as e:
            self.window.set_notice(f"تعذر تعديل التشغيل مع Windows: {e}")

    # ---------- النموذج ----------
    def _on_model_loaded(self, info):
        where = "GPU (CUDA)" if info.device == "cuda" else "CPU"
        text = f"النموذج {info.model_name} على {where} [{info.compute_type}] — تحميل {info.load_seconds:.1f} ث"
        if info.fallback_reason:
            text += f"\n⚠ يعمل على CPU (أبطأ) لأن: {info.fallback_reason}"
            self.sig_event.emit("tray_warning", {"message": f"التفريغ سيعمل على CPU وسيكون أبطأ: {info.fallback_reason}"})
        self.sig_model.emit(text)

    def load_model_now(self):
        def job():
            try:
                self.sig_model.emit("جارٍ تحميل النموذج…")
                self.pipeline.load()
            except ModelMissing as e:
                self.sig_model.emit(str(e))
            except Exception as e:
                log.exception("فشل تحميل النموذج")
                self.sig_model.emit(f"فشل تحميل النموذج: {e}")
        self.worker.submit(self._guard, job)

    def unload_model_now(self):
        if self.controller.busy:
            return
        self.worker.submit(self._guard, self.pipeline.transcriber.unload)
        self.window.set_model_status("تم تحرير النموذج من الذاكرة.")

    def _check_idle_unload(self):
        s, tr = self.settings, self.pipeline.transcriber
        if not tr.loaded or self.controller.busy:
            return
        idle_min = (time.monotonic() - tr.last_used) / 60
        if (not s.keep_model_loaded and idle_min > 0.5) or (s.keep_model_loaded and s.unload_after_minutes and idle_min >= s.unload_after_minutes):
            self.worker.submit(self._guard, tr.unload)
            self.window.set_model_status("تم تحرير النموذج بعد فترة خمول؛ سيُحمَّل عند الإملاء التالي.")

    def start_download(self, key):
        if self.downloading:
            return
        self.downloading = True
        self._download_cancel.clear()

        def run():
            try:
                download(key, progress=self.sig_download.emit, cancel=self._download_cancel)
                self.sig_download_done.emit(True, f"اكتمل تنزيل النموذج {key}.")
            except DownloadCancelled:
                self.sig_download_done.emit(False, "تم إيقاف التنزيل؛ يمكن استكماله لاحقًا.")
            except Exception as e:
                log.exception("فشل التنزيل")
                self.sig_download_done.emit(False, f"فشل التنزيل: {e}")

        threading.Thread(target=run, daemon=True, name="download").start()

    def cancel_download(self):
        self._download_cancel.set()

    def _on_download_done(self, ok, message):
        self.downloading = False
        self.window.download_finished(ok, message)
        if ok and self.settings.preload_on_start:
            self.load_model_now()

    def delete_model(self, key):
        if self.controller.busy:
            return
        def job():
            self.pipeline.transcriber.unload()
            delete_model(key)
        self.worker.submit(self._guard, job).add_done_callback(
            lambda _: self.sig_event.emit("models_changed", {}))

    # ---------- أحداث المتحكم ----------
    def _on_event(self, kind, kw):
        if kind == "state":
            self._on_state(kw["state"], kw.get("message", ""))
        elif kind == "notice":
            self.window.set_notice(kw["message"])
            self.overlay.show_status("idle", kw["message"][:60], auto_hide_ms=3000) if self.settings.show_overlay else None
        elif kind == "result":
            out, d = kw["output"], kw["delivery"]
            i = out.info
            info = (f"مدة الصوت {i.get('audio_seconds')} ث • كلام {i.get('speech_seconds')} ث • "
                    f"معالجة {i.get('process_seconds')} ث على {i.get('device', '').upper()} • {i.get('model')}")
            if i.get("fallback"):
                info += f"\n⚠ {i['fallback']}"
            self.window.set_result(out.raw_text, out.clean_text, info)
        elif kind == "tray_warning":
            self.tray.showMessage(APP_TITLE, kw["message"], QSystemTrayIcon.Warning, 6000)
        elif kind == "models_changed":
            self.window.refresh_models()
            self.window.set_model_status("تم حذف النموذج.")

    def _on_state(self, state, message: str):
        # Qt يحوّل Enum داخل dict إلى str عند عبور الإشارة
        st = state.value if isinstance(state, State) else str(state)
        self.window.set_state(st, message)
        self.tray.setIcon(self.icons.get(st, self.icons["idle"]))
        self.tray.setToolTip(f"{APP_TITLE} — {message[:100] or st}")
        self.tray_toggle.setText("إيقاف التسجيل" if st == "recording" else "ابدأ التسجيل")
        show = self.settings.show_overlay
        if st == "recording":
            self.level_timer.start()
            if show:
                self.overlay.show_status("recording", "● تسجيل 00:00", meter=True)
        else:
            self.level_timer.stop()
            if not show:
                self.overlay.hide()
            elif st == "processing":
                self.overlay.show_status("processing", "⏳ جارٍ التحويل إلى نص…")
            elif st == "done":
                self.overlay.show_status("done", "✓ " + (message.split("—")[0][:50] or "اكتمل"), auto_hide_ms=2500)
            elif st == "error":
                self.overlay.show_status("error", "⚠ " + message[:70], auto_hide_ms=5000)
                if not self.window.isVisible():
                    self.tray.showMessage(APP_TITLE, message, QSystemTrayIcon.Warning, 5000)
            else:
                self.overlay.show_status("idle", message[:60] or "جاهز", auto_hide_ms=1500)

    def _tick(self):
        secs = self.recorder.elapsed()
        self.window.set_level(self.recorder.level, secs)
        if self.settings.show_overlay and self.overlay.isVisible():
            self.overlay.label.setText(f"● تسجيل {int(secs // 60):02d}:{int(secs % 60):02d}")
            self.overlay.set_level(self.recorder.level)

    # ---------- الخروج ----------
    def quit(self):
        log.info("خروج")
        try:
            if self.bridge is not None:
                self.bridge.stop()
                self.bridge = None
            self.controller.cancel()
            if self.recorder.is_recording:
                self.recorder.abort()
            self._download_cancel.set()
            self.hotkeys.stop()
            self.tray.hide()
            self.overlay.hide()
        finally:
            self.worker.shutdown(wait=False, cancel_futures=True)
            self.qapp.quit()


ACK = b"ok"
ACK_TIMEOUT_MS = 4000


def _pid_file() -> Path:
    from .paths import data_dir

    return data_dir() / "qool.pid"


def _kill_hung_instance() -> None:
    """النسخة القديمة متصلة بالأنبوب لكنها لا ترد (معلّقة): أنهِها حتى تبدأ نسخة جديدة."""
    import psutil

    try:
        pid = int(_pid_file().read_text().strip())
        proc = psutil.Process(pid)
        if pid == os.getpid() or "python" not in proc.name().lower() or "qool" not in " ".join(proc.cmdline()):
            return
        parent = proc.parent()
        log.warning("النسخة السابقة (pid=%s) لا تستجيب؛ سيتم إنهاؤها", pid)
        proc.kill()
        proc.wait(5)
        # مشغّل البيئة الافتراضية (.venv\pythonw.exe) ينتهي وحده عادةً، لكن نتأكد
        if parent is not None and "python" in parent.name().lower() and "qool" in " ".join(parent.cmdline()):
            parent.kill()
    except (OSError, ValueError, psutil.Error) as e:
        log.warning("تعذّر إنهاء النسخة المعلّقة: %s", e)


def _single_instance(qapp, command: bytes) -> QLocalServer | None:
    """إن كانت نسخة تعمل: أرسل لها الأمر (show/quit) وارجع None.

    إن لم ترد النسخة القديمة خلال ACK_TIMEOUT_MS فهي معلّقة: تُنهى وتبدأ هذه النسخة مكانها.
    """
    name = f"{APP_NAME}-{getpass.getuser()}"
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        sock.write(command)
        sock.flush()
        sock.waitForBytesWritten(500)
        replied = sock.waitForReadyRead(ACK_TIMEOUT_MS) and bytes(sock.readAll()).startswith(ACK)
        sock.abort()
        if replied:
            return None
        _kill_hung_instance()
        if command == b"quit":
            return None
    elif command == b"quit":
        return None  # لا توجد نسخة تعمل
    QLocalServer.removeServer(name)
    server = QLocalServer()
    if not server.listen(name):
        log.warning("تعذّر حجز اسم النسخة الوحيدة: %s", server.errorString())
    try:
        _pid_file().write_text(str(os.getpid()))
    except OSError:
        pass
    return server


def _start_hang_watchdog(qapp) -> QTimer:
    """يسجّل مكدّس كل الخيوط في logs/hang.log إن توقفت الواجهة أكثر من HANG_SECONDS (لتشخيص التعليق)."""
    import faulthandler

    from .paths import logs_dir

    HANG_SECONDS = 10
    beat = [time.monotonic()]
    timer = QTimer(qapp, interval=1000, timeout=lambda: beat.__setitem__(0, time.monotonic()))
    timer.start()

    def watch():
        dumped = False
        last = time.monotonic()
        while True:
            time.sleep(2)
            now = time.monotonic()
            if now - last > 6:  # الجهاز كان نائمًا: ليس تعليقًا
                beat[0] = now
            last = now
            stalled = now - beat[0]
            if stalled > HANG_SECONDS and not dumped:
                dumped = True
                log.error("الواجهة لا تستجيب منذ %.0f ث؛ حفظ المكدّس في hang.log", stalled)
                with open(logs_dir() / "hang.log", "a", encoding="utf-8") as f:
                    f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} توقف {stalled:.0f} ث =====\n")
                    f.flush()
                    faulthandler.dump_traceback(f, all_threads=True)
            elif stalled < 2 and dumped:
                dumped = False
                log.info("الواجهة عادت تستجيب")

    threading.Thread(target=watch, name="hang-watchdog", daemon=True).start()
    return timer


def main(argv=None) -> int:
    from .logging_setup import setup_logging

    debug = "--debug" in (argv or sys.argv)
    setup_logging(debug)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setApplicationName(APP_NAME)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setLayoutDirection(Qt.RightToLeft)
    server = _single_instance(qapp, b"quit" if "--quit" in sys.argv else b"show")
    if server is None:
        return 0
    app = QoolApp(qapp)

    def on_connection():
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.waitForReadyRead(500)
        cmd = bytes(conn.readAll()).strip()
        conn.write(ACK)
        conn.flush()
        conn.waitForBytesWritten(500)
        if cmd == b"quit":
            app.quit()
        else:
            app.show_window()

    server.newConnection.connect(on_connection)
    watchdog = _start_hang_watchdog(qapp)  # noqa: F841 (يبقى حيًا طوال التشغيل)
    if "--show" in sys.argv:
        app.show_window()
    log.info("بدأ التطبيق")
    code = qapp.exec()
    log.info("انتهت حلقة الأحداث")
    # الميكروفون والاختصارات أُغلقت في quit(). نحرر PortAudio صراحة ثم ننهي العملية فورًا:
    # os._exit وحده استغرق ~30 ث بسبب تفريغ مكتبات (DLL detach) على هذا الجهاز،
    # والعامل قد يكون داخل تفريغ طويل لا يمكن مقاطعته.
    try:
        if "sounddevice" in sys.modules:
            sys.modules["sounddevice"]._terminate()
    except Exception:
        pass
    log.info("إنهاء العملية")
    logging.shutdown()
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), int(code))
    finally:
        os._exit(code)
