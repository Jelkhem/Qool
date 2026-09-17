"""محرر نصوص تجريبي بسيط في عملية مستقلة: يحفظ محتواه وعدد ضغطات Enter في ملف عند كل تغيير."""
import json
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPlainTextEdit

out_path, title = sys.argv[1], sys.argv[2]
app = QApplication(sys.argv)


class Editor(QPlainTextEdit):
    enters = 0

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            Editor.enters += 1
        super().keyPressEvent(e)
        dump()


ed = Editor()
ed.setWindowTitle(title)
ed.resize(500, 200)


def dump():
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"text": ed.toPlainText(), "enters": Editor.enters}, f, ensure_ascii=False)


ed.textChanged.connect(dump)
dump()
ed.show()
ed.raise_()
ed.activateWindow()
app.exec()
