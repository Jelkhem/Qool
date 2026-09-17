"""النافذة الرئيسية بتصميم حديث (RTL): صفحة الإملاء وصفحة الإعدادات."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_TITLE, __version__
from ..models import CATALOG, is_downloaded
from . import theme
from .widgets import Card, HotkeyEdit, RecordButton, ToggleSwitch, kbd, row, separator

STATUS_TEXT = {
    "idle": "جاهز",
    "recording": "جارٍ التسجيل",
    "processing": "جارٍ التحويل إلى نص",
    "done": "اكتمل",
    "error": "تنبيه",
}


class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.t = app.t
        s = app.settings
        self.setWindowTitle(f"{APP_TITLE} — إملاء صوتي")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(520, 820)
        self.setMinimumSize(460, 640)
        self._toggles: list[ToggleSwitch] = []

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(20, 16, 20, 14)
        outer.setSpacing(14)

        # ---------- الرأس ----------
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        h1 = QLabel(APP_TITLE)
        h1.setObjectName("h1")
        sub = QLabel("إملاء صوتي محلي باللهجة المصرية")
        sub.setObjectName("small")
        titles.addWidget(h1)
        titles.addWidget(sub)
        header.addLayout(titles)
        header.addStretch(1)
        navbar = QFrame()
        navbar.setObjectName("navbar")
        nl = QHBoxLayout(navbar)
        nl.setContentsMargins(4, 4, 4, 4)
        nl.setSpacing(2)
        self.nav_group = QButtonGroup(self)
        for i, name in enumerate(("الإملاء", "الإعدادات")):
            b = QPushButton(name)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.nav_group.addButton(b, i)
            nl.addWidget(b)
        self.nav_group.button(0).setChecked(True)
        header.addWidget(navbar)
        outer.addLayout(header)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._home_page())
        self.pages.addWidget(self._settings_page(s))
        self.nav_group.idClicked.connect(self.pages.setCurrentIndex)
        outer.addWidget(self.pages, 1)

        # ---------- التذييل ----------
        footer = QHBoxLayout()
        self.model_dot = QLabel("●")
        self.model_status = QLabel("")
        self.model_status.setObjectName("small")
        self.model_status.setWordWrap(True)
        footer.addWidget(self.model_dot)
        footer.addWidget(self.model_status, 1)
        outer.addLayout(footer)

        self.set_state("idle", "")
        self.set_bridge_state("off", "")
        self.update_hotkey_hint()
        self._paint_model_dot("idle")

    # =====================================================================
    # صفحة الإملاء
    # =====================================================================
    def _home_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        v.addWidget(self.banner)

        hero = Card()
        hero.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        hero.body.setSpacing(6)
        hero.body.setContentsMargins(18, 18, 18, 16)
        self.record_btn = RecordButton(self.t)
        self.record_btn.clicked.connect(self.app.toggle)
        self.record_btn.setToolTip("ابدأ/أوقف التسجيل")
        hero.body.addWidget(self.record_btn, 0, Qt.AlignHCenter)
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        status_row.addStretch(1)
        self.status_label = QLabel("جاهز")
        self.status_label.setObjectName("status")
        self.duration = QLabel("00:00")
        self.duration.setObjectName("timer")
        self.duration.setLayoutDirection(Qt.LeftToRight)
        self.duration.setVisible(False)
        status_row.addWidget(self.status_label)
        status_row.addWidget(self.duration)
        status_row.addStretch(1)
        hero.body.addLayout(status_row)
        self.message = QLabel("")
        self.message.setObjectName("muted")
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hero.body.addWidget(self.message)

        self.hint_row = QWidget()
        self.hint_row.setObjectName("clear")
        hint = QHBoxLayout(self.hint_row)
        hint.setContentsMargins(0, 4, 0, 0)
        hint.setSpacing(8)
        hint.addStretch(1)
        l1 = QLabel("اضغط")
        l1.setObjectName("muted")
        self.hotkey_chip = kbd("")
        l2 = QLabel("في أي برنامج للبدء والإيقاف")
        l2.setObjectName("muted")
        hint.addWidget(l1)
        hint.addWidget(self.hotkey_chip)
        hint.addWidget(l2)
        hint.addStretch(1)
        hero.body.addWidget(self.hint_row)
        self.cancel_btn = QPushButton("إلغاء")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.app.cancel)
        self.cancel_btn.setVisible(False)
        hero.body.addWidget(self.cancel_btn, 0, Qt.AlignHCenter)
        v.addWidget(hero)

        # ---------- جسر الميتينج ----------
        bridge = Card()
        bridge.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        bridge.body.setSpacing(8)
        btop = QHBoxLayout()
        btitles = QVBoxLayout()
        btitles.setSpacing(0)
        bt = QLabel("جسر الميتينج")
        bt.setObjectName("h2")
        bsub = QLabel("يخلّي ChatGPT يسمع الناس في Teams ويسمعك إنت كمان")
        bsub.setObjectName("small")
        btitles.addWidget(bt)
        btitles.addWidget(bsub)
        btop.addLayout(btitles)
        btop.addStretch(1)
        self.bridge_btn = QPushButton("تشغيل")
        self.bridge_btn.setCursor(Qt.PointingHandCursor)
        self.bridge_btn.setMinimumWidth(96)
        self.bridge_btn.clicked.connect(self.app.toggle_bridge)
        btop.addWidget(self.bridge_btn)
        bridge.body.addLayout(btop)
        self.bridge_meters = QWidget()
        self.bridge_meters.setObjectName("clear")
        mtr = QHBoxLayout(self.bridge_meters)
        mtr.setContentsMargins(0, 2, 0, 2)
        mtr.setSpacing(8)
        self.bridge_app_meter, self.bridge_mic_meter = QProgressBar(), QProgressBar()
        for label, bar in (("الميتينج", self.bridge_app_meter), ("مايكك", self.bridge_mic_meter)):
            lbl = QLabel(label)
            lbl.setObjectName("small")
            bar.setObjectName("meter")
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            mtr.addWidget(lbl)
            mtr.addWidget(bar, 1)
        bridge.body.addWidget(self.bridge_meters)
        self.bridge_duck = QLabel("")
        self.bridge_duck.setObjectName("small")
        bridge.body.addWidget(self.bridge_duck)
        self.bridge_status = QLabel("")
        self.bridge_status.setObjectName("small")
        self.bridge_status.setWordWrap(True)
        bridge.body.addWidget(self.bridge_status)
        bhint = QHBoxLayout()
        bhint.setSpacing(6)
        bh1 = QLabel("الاختصار")
        bh1.setObjectName("small")
        self.bridge_chip = kbd("")
        bh2 = QLabel("·  مايك ChatGPT: CABLE Output")
        bh2.setObjectName("small")
        bhint.addWidget(bh1)
        bhint.addWidget(self.bridge_chip)
        bhint.addWidget(bh2)
        bhint.addStretch(1)
        bridge.body.addLayout(bhint)
        v.addWidget(bridge)

        # ---------- النتيجة ----------
        res = Card()
        top = QHBoxLayout()
        t = QLabel("آخر نص")
        t.setObjectName("h2")
        top.addWidget(t)
        top.addStretch(1)
        segbar = QFrame()
        segbar.setObjectName("segbar")
        sl = QHBoxLayout(segbar)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.seg_group = QButtonGroup(self)
        for i, name in enumerate(("المنظّف", "الخام")):
            b = QPushButton(name)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.seg_group.addButton(b, i)
            sl.addWidget(b)
        self.seg_group.button(0).setChecked(True)
        top.addWidget(segbar)
        res.body.addLayout(top)

        self.result_stack = QStackedWidget()
        self.result_stack.setObjectName("clear")
        self.clean_edit = QPlainTextEdit()
        self.raw_edit = QPlainTextEdit()
        self.raw_edit.setReadOnly(True)
        for e in (self.clean_edit, self.raw_edit):
            e.setObjectName("result")
            e.setLayoutDirection(Qt.RightToLeft)
            e.setPlaceholderText("سيظهر هنا آخر نص تمليه…")
            e.setMinimumHeight(110)
            self.result_stack.addWidget(e)
        self.seg_group.idClicked.connect(self.result_stack.setCurrentIndex)
        res.body.addWidget(self.result_stack, 1)

        actions = QHBoxLayout()
        copy_btn = QPushButton("نسخ")
        copy_btn.setObjectName("primary")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(lambda: self.app.copy_text(self.current_text()))
        retry_btn = QPushButton("إعادة محاولة اللصق")
        retry_btn.setCursor(Qt.PointingHandCursor)
        retry_btn.setToolTip("بعد الضغط انقر داخل خانة الكتابة الأصلية خلال 6 ثوانٍ")
        retry_btn.clicked.connect(lambda: self.app.retry_paste(self.current_text()))
        actions.addWidget(copy_btn)
        actions.addWidget(retry_btn)
        actions.addStretch(1)
        res.body.addLayout(actions)
        self.info = QLabel("")
        self.info.setObjectName("small")
        self.info.setWordWrap(True)
        res.body.addWidget(self.info)
        v.addWidget(res, 1)
        return page

    # =====================================================================
    # صفحة الإعدادات
    # =====================================================================
    def _settings_page(self, s):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 6, 0)
        v.setSpacing(14)
        scroll.setWidget(inner)

        # الاختصارات
        c = Card("الاختصارات", "انقر على الخانة ثم اضغط الاختصار الجديد مباشرة.")
        self.hotkey_edit = HotkeyEdit(s.hotkey)
        self.cancel_hotkey_edit = HotkeyEdit(s.cancel_hotkey)
        for e in (self.hotkey_edit, self.cancel_hotkey_edit):
            e.capture_started.connect(self.app.suspend_hotkeys)
            e.capture_ended.connect(self.app.resume_hotkeys)
        self.hotkey_edit.captured.connect(lambda combo: self.app.apply_hotkeys(combo, self.app.settings.cancel_hotkey))
        self.cancel_hotkey_edit.captured.connect(lambda combo: self.app.apply_hotkeys(self.app.settings.hotkey, combo))
        c.body.addWidget(row("بدء / إيقاف التسجيل", self.hotkey_edit))
        c.body.addWidget(row("إلغاء", self.cancel_hotkey_edit))
        self.hotkey_status = QLabel("")
        self.hotkey_status.setObjectName("small")
        self.hotkey_status.setWordWrap(True)
        c.body.addWidget(self.hotkey_status)
        v.addWidget(c)

        # الميكروفون والتسجيل
        c = Card("الميكروفون")
        mic = QWidget()
        mic.setObjectName("clear")
        ml = QHBoxLayout(mic)
        ml.setContentsMargins(0, 0, 0, 0)
        self.mic_combo = QComboBox()
        self.mic_combo.currentIndexChanged.connect(self._mic_changed)
        refresh = QPushButton("تحديث")
        refresh.clicked.connect(self.refresh_mics)
        ml.addWidget(self.mic_combo, 1)
        ml.addWidget(refresh)
        c.body.addWidget(mic)
        max_min = QSpinBox()
        max_min.setRange(1, 60)
        max_min.setValue(s.max_recording_minutes)
        max_min.setSuffix(" دقيقة")
        max_min.valueChanged.connect(lambda val: self.app.update_setting("max_recording_minutes", val))
        c.body.addWidget(row("أقصى مدة للتسجيل", max_min))
        v.addWidget(c)

        # جسر الميتينج
        c = Card("جسر الميتينج", "بيدمج صوت الميتينج مع مايكك ويبعتهم على «CABLE Input». "
                                 "سيب سمّاعة Teams على الهيدفون، ومايك ChatGPT = «CABLE Output».")
        self.bridge_hotkey_edit = HotkeyEdit(s.bridge_hotkey)
        self.bridge_hotkey_edit.capture_started.connect(self.app.suspend_hotkeys)
        self.bridge_hotkey_edit.capture_ended.connect(self.app.resume_hotkeys)
        self.bridge_hotkey_edit.captured.connect(self.app.apply_bridge_hotkey)
        c.body.addWidget(row("تشغيل / إيقاف الجسر", self.bridge_hotkey_edit))
        self.bridge_hotkey_status = QLabel("")
        self.bridge_hotkey_status.setObjectName("small")
        self.bridge_hotkey_status.setWordWrap(True)
        c.body.addWidget(self.bridge_hotkey_status)
        self.bridge_mic_combo = QComboBox()
        self.bridge_mic_combo.currentIndexChanged.connect(self._bridge_mic_changed)
        c.body.addWidget(row("مايك الجسر", self.bridge_mic_combo))
        v.addWidget(c)

        # اللصق
        c = Card("اللصق", "لا يتم ضغط Enter أو إرسال أي رسالة أبدًا.")
        c.body.addWidget(self._toggle("لصق تلقائي في الخانة التي بدأت منها", s.auto_paste, "auto_paste"))
        c.body.addWidget(self._toggle("وضع المراجعة: انسخ فقط دون لصق", s.review_mode, "review_mode"))
        c.body.addWidget(self._toggle("إظهار المؤشر العائم أثناء التسجيل", s.show_overlay, "show_overlay"))
        v.addWidget(c)

        # النموذج
        c = Card("النموذج والمعالجة", "كل المعالجة على جهازك. التنزيل مرة واحدة فقط.")
        self.model_combo = QComboBox()
        self.refresh_models()
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        c.body.addWidget(self.model_combo)
        mb = QHBoxLayout()
        self.download_btn = QPushButton("تنزيل")
        self.download_btn.setObjectName("primary")
        self.download_btn.clicked.connect(self._download_clicked)
        self.delete_btn = QPushButton("حذف النموذج")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.clicked.connect(self._delete_clicked)
        mb.addWidget(self.download_btn)
        mb.addWidget(self.delete_btn)
        mb.addStretch(1)
        c.body.addLayout(mb)
        self.download_bar = QProgressBar()
        self.download_bar.setObjectName("download")
        self.download_bar.setVisible(False)
        c.body.addWidget(self.download_bar)
        c.body.addWidget(separator())

        self.device_combo = QComboBox()
        for val, label in (("auto", "تلقائي (GPU ثم CPU)"), ("cuda", "GPU (CUDA)"), ("cpu", "CPU")):
            self.device_combo.addItem(label, val)
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(s.device)))
        self.device_combo.currentIndexChanged.connect(
            lambda: self.app.update_setting("device", self.device_combo.currentData(), reload=True))
        c.body.addWidget(row("طريقة المعالجة", self.device_combo))
        self.ct_combo = QComboBox()
        for val in ("int8_float16", "float16", "int8"):
            self.ct_combo.addItem(val, val)
        self.ct_combo.setCurrentIndex(max(0, self.ct_combo.findData(s.gpu_compute_type)))
        self.ct_combo.currentIndexChanged.connect(
            lambda: self.app.update_setting("gpu_compute_type", self.ct_combo.currentData(), reload=True))
        c.body.addWidget(row("دقة الحساب على GPU", self.ct_combo))
        beam = QSpinBox()
        beam.setRange(1, 10)
        beam.setValue(s.beam_size)
        beam.valueChanged.connect(lambda val: self.app.update_setting("beam_size", val))
        c.body.addWidget(row("Beam (أعلى = أدق وأبطأ)", beam))
        c.body.addWidget(separator())
        c.body.addWidget(self._toggle("تحميل النموذج عند بدء التطبيق", s.preload_on_start, "preload_on_start"))
        c.body.addWidget(self._toggle("إبقاء النموذج في الذاكرة (أسرع)", s.keep_model_loaded, "keep_model_loaded"))
        unload = QSpinBox()
        unload.setRange(0, 600)
        unload.setValue(s.unload_after_minutes)
        unload.setSuffix(" دقيقة")
        unload.setSpecialValueText("أبدًا")
        unload.valueChanged.connect(lambda val: self.app.update_setting("unload_after_minutes", val))
        c.body.addWidget(row("تحرير الذاكرة بعد خمول", unload))
        lb = QHBoxLayout()
        load_now = QPushButton("تحميل الآن")
        load_now.clicked.connect(self.app.load_model_now)
        unload_now = QPushButton("تحرير الذاكرة الآن")
        unload_now.clicked.connect(self.app.unload_model_now)
        lb.addWidget(load_now)
        lb.addWidget(unload_now)
        lb.addStretch(1)
        c.body.addLayout(lb)
        cp = QWidget()
        cp.setObjectName("clear")
        cpl = QHBoxLayout(cp)
        cpl.setContentsMargins(0, 0, 0, 0)
        self.custom_path = QLineEdit(s.custom_model_path)
        self.custom_path.setPlaceholderText("اختياري: مجلد نموذج CTranslate2")
        self.custom_path.editingFinished.connect(
            lambda: self.app.update_setting("custom_model_path", self.custom_path.text().strip(), reload=True))
        browse = QPushButton("…")
        browse.clicked.connect(self._browse_model)
        cpl.addWidget(self.custom_path, 1)
        cpl.addWidget(browse)
        c.body.addWidget(row("نموذج مخصص", cp))
        v.addWidget(c)

        # اللغة
        c = Card("اللغة والتنظيف", "النص الخام متاح دائمًا لاسترجاع أي حذف.")
        self.prompt_edit = QPlainTextEdit(s.initial_prompt)
        self.prompt_edit.setMaximumHeight(70)
        self.prompt_edit.textChanged.connect(
            lambda: self.app.update_setting("initial_prompt", self.prompt_edit.toPlainText()))
        c.body.addWidget(QLabel("سياق للنموذج"))
        c.body.addWidget(self.prompt_edit)
        self.vocab_edit = QLineEdit(s.vocabulary)
        self.vocab_edit.editingFinished.connect(lambda: self.app.update_setting("vocabulary", self.vocab_edit.text()))
        c.body.addWidget(row("مصطلحات وأسماء", self.vocab_edit))
        c.body.addWidget(self._toggle("تنظيف محافظ (ترقيم، مسافات، إزالة «اممم»)", s.cleanup_enabled, "cleanup_enabled"))
        gap = QDoubleSpinBox()
        gap.setRange(0, 30)
        gap.setSingleStep(0.5)
        gap.setValue(s.paragraph_gap_seconds)
        gap.setSuffix(" ث")
        gap.setSpecialValueText("بلا فقرات")
        gap.valueChanged.connect(lambda val: self.app.update_setting("paragraph_gap_seconds", float(val)))
        c.body.addWidget(row("سطر جديد بعد سكتة", gap))
        v.addWidget(c)

        # عام
        c = Card("عام")
        self.theme_combo = QComboBox()
        for val, label in (("system", "حسب Windows"), ("dark", "داكن"), ("light", "فاتح")):
            self.theme_combo.addItem(label, val)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(s.theme)))
        self.theme_combo.currentIndexChanged.connect(lambda: self.app.set_theme(self.theme_combo.currentData()))
        c.body.addWidget(row("المظهر", self.theme_combo))
        self.startup_cb = ToggleSwitch("التشغيل مع Windows", self.t)
        self.startup_cb.setChecked(s.start_with_windows)
        self.startup_cb.toggled.connect(self.app.set_start_with_windows)
        self._toggles.append(self.startup_cb)
        c.body.addWidget(self.startup_cb)
        bottom = QHBoxLayout()
        ver = QLabel(f"الإصدار {__version__}")
        ver.setObjectName("small")
        quit_btn = QPushButton("خروج من التطبيق")
        quit_btn.setObjectName("danger")
        quit_btn.clicked.connect(self.app.quit)
        bottom.addWidget(ver)
        bottom.addStretch(1)
        bottom.addWidget(quit_btn)
        c.body.addLayout(bottom)
        v.addWidget(c)
        v.addStretch(1)
        return scroll

    def _toggle(self, label, value, key):
        tg = ToggleSwitch(label, self.t)
        tg.setChecked(value)
        tg.toggled.connect(lambda val: self.app.update_setting(key, bool(val)))
        self._toggles.append(tg)
        return tg

    # =====================================================================
    # أحداث الإعدادات
    # =====================================================================
    def refresh_mics(self):
        names = self.app.list_mics()
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        self.mic_combo.addItem("الافتراضي في Windows", "")
        for n in names:
            self.mic_combo.addItem(n, n)
        cur = self.app.settings.microphone
        idx = self.mic_combo.findData(cur)
        if cur and idx < 0:
            self.mic_combo.addItem(f"{cur} (غير متصل)", cur)
            idx = self.mic_combo.count() - 1
        self.mic_combo.setCurrentIndex(max(0, idx))
        self.mic_combo.blockSignals(False)
        self._fill_bridge_mics()

    def _mic_changed(self):
        self.app.update_setting("microphone", self.mic_combo.currentData() or "")

    def _fill_bridge_mics(self):
        combo = self.bridge_mic_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("الافتراضي في Windows", "")
        for n in self.app.list_bridge_mics():
            combo.addItem(n, n)
        cur = self.app.settings.bridge_microphone
        idx = combo.findData(cur)
        if cur and idx < 0:
            combo.addItem(f"{cur} (غير متصل)", cur)
            idx = combo.count() - 1
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)

    def _bridge_mic_changed(self):
        self.app.set_bridge_mic(self.bridge_mic_combo.currentData() or "")

    def refresh_models(self):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for key, info in CATALOG.items():
            mark = "✓ منزَّل" if is_downloaded(key) else f"~{info.size_mb} MB"
            self.model_combo.addItem(f"{info.label}  ·  {mark}", key)
        self.model_combo.setCurrentIndex(max(0, self.model_combo.findData(self.app.settings.model)))
        self.model_combo.blockSignals(False)

    def _model_changed(self):
        self.app.update_setting("model", self.model_combo.currentData(), reload=True)

    def _download_clicked(self):
        key = self.model_combo.currentData()
        if self.app.downloading:
            self.app.cancel_download()
            return
        info = CATALOG[key]
        if is_downloaded(key):
            QMessageBox.information(self, APP_TITLE, "النموذج منزَّل بالفعل.")
            return
        if QMessageBox.question(self, APP_TITLE,
                                f"سيتم تنزيل النموذج {key} بحجم ~{info.size_mb} MB من Hugging Face مرة واحدة فقط.\nمتابعة؟") == QMessageBox.Yes:
            self.app.start_download(key)

    def _delete_clicked(self):
        key = self.model_combo.currentData()
        if not is_downloaded(key):
            return
        if QMessageBox.question(self, APP_TITLE, f"حذف ملفات النموذج {key} من الجهاز؟") == QMessageBox.Yes:
            self.app.delete_model(key)

    def _browse_model(self):
        d = QFileDialog.getExistingDirectory(self, "اختر مجلد نموذج CTranslate2")
        if d:
            self.custom_path.setText(d)
            self.app.update_setting("custom_model_path", d, reload=True)

    # =====================================================================
    # تحديثات من التطبيق
    # =====================================================================
    def apply_theme(self, t: dict):
        self.t = t
        self.record_btn.set_theme(t)
        for tg in self._toggles:
            tg.t = t
            tg.update()
        theme.apply_window_chrome(self, t)
        self._paint_model_dot(getattr(self, "_model_kind", "idle"))
        self.set_bridge_state(*self._bridge_last)

    def showEvent(self, e):
        super().showEvent(e)
        theme.apply_window_chrome(self, self.t)

    def current_text(self) -> str:
        return (self.clean_edit if self.result_stack.currentIndex() == 0 else self.raw_edit).toPlainText()

    def set_state(self, state: str, message: str):
        self.record_btn.set_state(state)
        self.record_btn.setEnabled(state != "processing")
        self.status_label.setText(STATUS_TEXT.get(state, ""))
        color = {"recording": self.t["red"], "processing": self.t["amber"], "done": self.t["green"],
                 "error": self.t["danger"]}.get(state, self.t["text"])
        self.status_label.setStyleSheet(f"color:{color};")
        if message:
            self.message.setText(message)
        busy = state in ("recording", "processing")
        self.cancel_btn.setVisible(busy)
        self.hint_row.setVisible(not busy)
        self.duration.setVisible(state == "recording")
        if state != "recording":
            self.record_btn.set_level(0)
            self.duration.setText("00:00")

    def set_bridge_state(self, kind: str, message: str):
        """kind: off | on | waiting | error"""
        self._bridge_last = (kind, message)
        running = kind in ("on", "waiting")
        self.bridge_btn.setText("إيقاف" if running else "تشغيل")
        self.bridge_btn.setObjectName("danger" if running else "primary")
        self.bridge_btn.style().unpolish(self.bridge_btn)
        self.bridge_btn.style().polish(self.bridge_btn)
        color = {"on": self.t["green"], "waiting": self.t["amber"], "error": self.t["danger"]}.get(kind, self.t["muted"])
        if not message and kind == "off":
            message = "مقفول. افتح Teams ودوس «تشغيل»."
        self.bridge_status.setText(("● " if running else "") + message)
        self.bridge_status.setStyleSheet(f"color:{color};")
        self.bridge_meters.setVisible(running)
        if not running:
            self.set_bridge_levels(0.0, 0.0)

    def set_bridge_levels(self, app_level: float, mic_level: float, ducking: bool = False):
        self.bridge_app_meter.setValue(int(app_level * 100))
        self.bridge_mic_meter.setValue(int(mic_level * 100))
        self.bridge_duck.setText("ChatGPT بيتكلم، فصوت الميتينج متكتوم عنه لحظيًا." if ducking else "")

    def set_bridge_hotkey_status(self, text: str, ok: bool):
        self.bridge_hotkey_status.setText(text)
        self.bridge_hotkey_status.setStyleSheet(f"color:{self.t['green'] if ok else self.t['danger']};")
        self.bridge_hotkey_edit.set_value(self.app.settings.bridge_hotkey)
        self.update_hotkey_hint()
        if not ok:
            self.show_banner(text)

    def set_level(self, level: float, seconds: float):
        self.record_btn.set_level(level)
        self.duration.setText(f"{int(seconds // 60):02d}:{int(seconds % 60):02d}")

    def set_notice(self, message: str):
        self.message.setText(message)

    def set_result(self, raw: str, clean: str, info: str):
        self.raw_edit.setPlainText(raw)
        self.clean_edit.setPlainText(clean)
        self.seg_group.button(0).setChecked(True)
        self.result_stack.setCurrentIndex(0)
        self.info.setText(info)

    def _paint_model_dot(self, kind: str):
        self._model_kind = kind
        color = {"gpu": self.t["green"], "cpu": self.t["amber"], "error": self.t["danger"]}.get(kind, self.t["muted"])
        self.model_dot.setStyleSheet(f"color:{color}; font-size:11pt;")

    def set_model_status(self, text: str):
        self.model_status.setText(text)
        if "CPU" in text and ("⚠" in text or "على CPU" in text):
            self._paint_model_dot("cpu")
        elif "GPU" in text:
            self._paint_model_dot("gpu")
        elif "فشل" in text or "غير منزَّل" in text or "غير صالح" in text:
            self._paint_model_dot("error")
        else:
            self._paint_model_dot("idle")

    def show_banner(self, text: str):
        self.banner.setText(text)
        self.banner.setVisible(bool(text))

    def set_hotkey_status(self, text: str, ok: bool):
        self.hotkey_status.setText(text)
        self.hotkey_status.setStyleSheet(f"color:{self.t['green'] if ok else self.t['danger']};")
        self.hotkey_edit.set_value(self.app.settings.hotkey)
        self.cancel_hotkey_edit.set_value(self.app.settings.cancel_hotkey)
        self.update_hotkey_hint()
        if not ok:
            self.show_banner(text)

    def update_hotkey_hint(self):
        s = self.app.settings
        self.hotkey_chip.setText(s.hotkey)
        self.bridge_chip.setText(s.bridge_hotkey or "—")
        self.cancel_btn.setText("إلغاء")
        self.cancel_btn.setToolTip(f"اختصار الإلغاء: {s.cancel_hotkey}" if s.cancel_hotkey else "")

    def set_download_progress(self, done: int, total: int):
        self.download_bar.setVisible(True)
        self.download_bar.setRange(0, 1000)
        self.download_bar.setValue(int(done / total * 1000) if total else 0)
        self.download_bar.setFormat(f"{done / 2**20:.0f} / {total / 2**20:.0f} MB")
        self.download_bar.setTextVisible(True)
        self.download_btn.setText("إيقاف التنزيل")

    def download_finished(self, ok: bool, message: str):
        self.download_btn.setText("تنزيل")
        self.download_bar.setVisible(not ok)
        self.refresh_models()
        self.set_notice(message)

    def closeEvent(self, e):
        # الإغلاق يخفي النافذة؛ الخروج من شريط النظام أو زر «خروج من التطبيق»
        e.ignore()
        self.hide()
        self.app.tray_hint_once()
