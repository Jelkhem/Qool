"""اختبار انحدار: Qt يحوّل Enum داخل dict إلى str عند عبور الإشارة، ويجب ألا ينهار _on_state."""
import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QObject, Signal  # noqa: E402

from qool.app import QoolApp  # noqa: E402
from qool.config import Settings  # noqa: E402
from qool.controller import State  # noqa: E402


class Emitter(QObject):
    sig = Signal(str, dict)


def _stub():
    s = MagicMock()
    s.settings = Settings(show_overlay=True)
    s.icons = {"idle": object()}
    s.window.isVisible.return_value = True
    return s


def test_state_through_qt_signal_does_not_crash():
    app = QCoreApplication.instance() or QCoreApplication([])
    stub = _stub()
    received = []
    e = Emitter()
    e.sig.connect(lambda kind, kw: (received.append(type(kw["state"])), QoolApp._on_state(stub, kw["state"], kw["message"])))
    for st in State:
        e.sig.emit("state", {"state": st, "message": "رسالة"})
    app.processEvents()
    assert len(received) == len(State)
    shown = [c.args[0] for c in stub.window.set_state.call_args_list]
    assert shown == [st.value for st in State]
    stub.level_timer.start.assert_called()  # حالة التسجيل شغّلت مؤقت المستوى


def test_state_accepts_enum_directly():
    stub = _stub()
    QoolApp._on_state(stub, State.DONE, "تم")
    stub.window.set_state.assert_called_with("done", "تم")
