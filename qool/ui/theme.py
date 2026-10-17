"""ثيم حديث (داكن/فاتح) يتبع إعداد Windows، مع شريط عنوان متناسق."""
from __future__ import annotations

import ctypes

# ألوان هوية Claude: خلفية كريمية دافية، نص شبه أسود، ولون مميز طوبي (clay).
DARK = dict(
    bg="#141413", surface="#1F1E1D", surface2="#2B2A28", border="#3B3A37", text="#FAF9F5",
    muted="#B0AEA5", accent="#D97757", accent_hover="#E28E73", on_accent="#1A1917",
    red="#E07A68", amber="#D9A757", green="#9BB07C", danger="#E07A68",
)
LIGHT = dict(
    bg="#F0EEE6", surface="#FAF9F5", surface2="#E8E6DC", border="#DAD8CE", text="#141413",
    muted="#5C5B54", accent="#B4502F", accent_hover="#9C4228", on_accent="#FAF9F5",
    red="#B03A2B", amber="#9A6B1E", green="#5F7346", danger="#B03A2B",
)

FONT_FAMILY = '"Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"'


def system_prefers_dark() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def tokens(mode: str = "system") -> dict:
    if mode == "dark" or (mode == "system" and system_prefers_dark()):
        return dict(DARK, name="dark")
    return dict(LIGHT, name="light")


def stylesheet(t: dict) -> str:
    return f"""
    QWidget#appRoot, QMainWindow, QDialog {{ background: {t['bg']}; }}
    QWidget {{ background: transparent; color: {t['text']}; font-family: {FONT_FAMILY}; font-size: 11pt; }}
    QLabel {{ background: transparent; }}
    QLabel#badge {{ background: {t["surface2"]}; color: {t["muted"]}; border-radius: 12px; padding: 8px 14px; font-size: 10pt; }}
    QLabel#h1 {{ font-size: 24pt; font-weight: 600; }}
    QLabel#h2 {{ font-size: 11.5pt; font-weight: 600; }}
    QLabel#muted {{ color: {t['muted']}; }}
    QLabel#small {{ color: {t['muted']}; font-size: 10pt; }}
    QLabel#status {{ font-size: 13pt; font-weight: 600; }}
    QLabel#timer {{ font-size: 16pt; font-weight: 300; font-family: "Segoe UI Variable Display", "Segoe UI"; }}
    QLabel#kbd {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-bottom: 3px solid {t['border']};
                 border-radius: 8px; padding: 3px 10px; font-weight: 600; font-family: "Segoe UI"; }}
    QLabel#banner {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 8px 12px; }}
    QFrame#card {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 16px; }}
    QWidget#clear, QStackedWidget#clear {{ background: transparent; }}
    QFrame#sep {{ background: {t['border']}; max-height: 1px; min-height: 1px; border: none; }}

    QPushButton {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 10px;
                  padding: 8px 16px; font-weight: 500; }}
    QPushButton:focus {{ border: 1px solid {t["accent"]}; }}
    QPushButton:hover {{ border-color: {t['accent']}; }}
    QPushButton:pressed {{ background: {t['border']}; }}
    QPushButton:disabled {{ color: {t['muted']}; border-color: {t['surface2']}; }}
    QPushButton#primary {{ background: {t['accent']}; color: {t['on_accent']}; border: none; }}
    QPushButton#primary:hover {{ background: {t['accent_hover']}; }}
    QPushButton#ghost {{ background: transparent; border: none; color: {t['muted']}; padding: 6px 10px; }}
    QPushButton#ghost:hover {{ color: {t['text']}; }}
    QPushButton#danger {{ background: transparent; color: {t['danger']}; border: 1px solid {t['border']}; }}
    QPushButton#nav {{ background: transparent; border: none; border-radius: 10px; padding: 8px 20px;
                      color: {t['muted']}; font-weight: 600; }}
    QPushButton#nav:checked {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']}; }}
    QPushButton#seg {{ background: transparent; border: none; border-radius: 8px; padding: 5px 14px; color: {t['muted']}; }}
    QPushButton#seg:checked {{ background: {t['surface2']}; color: {t['text']}; }}
    QFrame#navbar, QFrame#segbar {{ background: {t['surface2']}; border-radius: 12px; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
        background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 7px 10px;
        selection-background-color: {t['accent']}; selection-color: {t['on_accent']}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{ border-color: {t['accent']}; }}
    QPlainTextEdit#result {{ background: transparent; border: none; font-size: 13pt; padding: 4px; }}
    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px;
                                  selection-background-color: {t['surface2']}; selection-color: {t['text']}; padding: 4px; outline: none; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        border: none; background: transparent; width: 16px; }}

    QProgressBar {{ background: {t['surface2']}; border: none; border-radius: 4px; max-height: 8px; text-align: center; color: transparent; }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
    QProgressBar#download {{ max-height: 18px; color: {t['text']}; font-size: 8.5pt; }}
    QProgressBar#meter {{ max-height: 6px; min-height: 6px; border-radius: 3px; }}
    QProgressBar#meter::chunk {{ background: {t['green']}; border-radius: 3px; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 4px; min-height: 36px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    QToolTip {{ background: {t['surface2']}; color: {t['text']}; border: 1px solid {t['border']}; padding: 6px; border-radius: 6px; }}
    QMenu {{ background: {t['surface']}; border: 1px solid {t['border']}; padding: 6px; border-radius: 10px; }}
    QMenu::item {{ padding: 8px 22px; border-radius: 6px; background: transparent; }}
    QMenu::item:selected {{ background: {t['surface2']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 8px; }}
    QMessageBox QLabel {{ font-size: 10.5pt; }}
    """


def apply_window_chrome(widget, t: dict) -> None:
    """شريط عنوان داكن/فاتح بلون الخلفية وحواف دائرية على Windows 11 (بدون أي تغيير على النظام)."""
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1 if t["name"] == "dark" else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))  # immersive dark mode
        hexc = t["bg"].lstrip("#")
        colorref = ctypes.c_int(int(hexc[4:6] + hexc[2:4] + hexc[0:2], 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref))  # caption color
        corner = ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))  # round corners
    except Exception:
        pass
