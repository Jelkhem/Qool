"""سجلات أعطال محلية لا تحتوي النص أو الصوت."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .paths import logs_dir


def setup_logging(debug: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fh = RotatingFileHandler(logs_dir() / "qool.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"))
    root.addHandler(fh)
    if sys.stderr is not None and debug:
        root.addHandler(logging.StreamHandler())

    def excepthook(t, v, tb):
        logging.getLogger("qool").critical("استثناء غير معالج", exc_info=(t, v, tb))

    sys.excepthook = excepthook
