"""يرسم النافذة الرئيسية إلى صور PNG للمراجعة البصرية (بدون تسجيل أو اختصارات).

  python scripts/render_ui.py OUT_DIR [dark|light]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from qool.config import Settings
from qool.ui import theme
from qool.ui.main_window import MainWindow
from qool.ui.overlay import Overlay

out_dir = Path(sys.argv[1])
mode = sys.argv[2] if len(sys.argv) > 2 else "dark"
out_dir.mkdir(parents=True, exist_ok=True)


class StubApp:
    def __init__(self):
        self.settings = Settings(hotkey="Ctrl+Alt+Q", theme=mode)
        self.downloading = False
        self.t = theme.tokens(mode)

    def __getattr__(self, name):
        return lambda *a, **k: []


qapp = QApplication(sys.argv)
qapp.setLayoutDirection(Qt.RightToLeft)
stub = StubApp()
qapp.setStyleSheet(theme.stylesheet(stub.t))
w = MainWindow(stub)
w.show_banner("Ctrl+Alt+Space مستخدم من برنامج آخر على هذا الجهاز، لذلك تم استخدام Ctrl+Alt+Q.")
w.set_state("done", "أُرسل أمر اللصق إلى claude.exe. راجع النص قبل الإرسال.")
w.set_level(0.0, 12)
w.set_result("اممم أنا عايز أعمل تطبيق يكتب الكلام بالمصري",
             "أنا عايز أعمل تطبيق يكتب الكلام بالمصري.",
             "صوت 5.2 ث • كلام 4.8 ث • معالجة 0.6 ث على CUDA • large-v3-turbo")
w.set_model_status("النموذج large-v3-turbo على GPU (CUDA) [int8_float16] — تحميل 4.2 ث")
w.show()
qapp.processEvents()
w.grab().save(str(out_dir / f"home_{mode}.png"))

w.set_state("recording", "جارٍ التسجيل… اضغط الاختصار مرة أخرى للإيقاف.")
w.set_level(0.6, 37)
w.record_btn._shown_level = 0.6
qapp.processEvents()
w.grab().save(str(out_dir / f"recording_{mode}.png"))

w.pages.setCurrentIndex(1)
w.nav_group.button(1).setChecked(True)
qapp.processEvents()
w.grab().save(str(out_dir / f"settings_{mode}.png"))

ov = Overlay()
ov.set_level(0.7)
ov.show_status("recording", "تسجيل 00:37", meter=True)
for _ in range(8):
    ov._tick()
qapp.processEvents()
ov.grab().save(str(out_dir / "overlay.png"))
print("saved to", out_dir)
