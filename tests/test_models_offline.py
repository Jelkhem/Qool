"""تنزيل النماذج على أجهزة فيها حجب: رسالة مفهومة، ومصدر بديل، واستيراد بدون إنترنت."""
import json
import socket
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from qool import models as M


def _fake_model(dir_: Path) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    for name in M.REQUIRED_FILES:
        (dir_ / name).write_text("x", encoding="utf-8")
    return dir_


def test_blocked_download_gives_arabic_hint():
    err = urllib.error.URLError(ConnectionResetError(10054, "forcibly closed"))
    msg = M.blocked_message(err)
    assert "محجوب" in msg and "استيراد" in msg
    assert "huggingface.co" in msg          # العناوين اللي يطلبها المستخدم من IT
    assert "10054" in msg                   # التفاصيل الفنية موجودة برضه


def test_endpoint_can_point_at_a_mirror(monkeypatch):
    assert M.endpoint() == M.DEFAULT_ENDPOINT
    monkeypatch.setenv("QOOL_MODEL_ENDPOINT", "https://hf-mirror.com/")
    assert M.endpoint() == "https://hf-mirror.com"


def test_download_urls_follow_the_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("QOOL_MODEL_ENDPOINT", "https://mirror.example")
    seen = []

    class R:
        status = 200
        def read(self, *a):
            return b""
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        seen.append(url)
        if "/api/models/" in url:
            class J(R):
                def read(self, *a):
                    return json.dumps([{"type": "file", "path": "config.json", "size": 0}]).encode()
            return J()
        return R()

    with patch("urllib.request.urlopen", fake_urlopen):
        M.download("small")
    assert all(u.startswith("https://mirror.example") for u in seen), seen


def test_import_model_from_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("QOOL_DATA_DIR", str(tmp_path / "data"))
    src = _fake_model(tmp_path / "usb" / "large-v3-turbo")
    name = M.import_model(src)
    assert name == "large-v3-turbo" and M.is_downloaded(name)


def test_import_model_from_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("QOOL_DATA_DIR", str(tmp_path / "data"))
    src = _fake_model(tmp_path / "pack" / "medium")
    zip_path = tmp_path / "medium.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        for f in src.iterdir():
            z.write(f, f"medium/{f.name}")
    name = M.import_model(zip_path)
    assert name == "medium" and M.is_downloaded(name)


def test_import_rejects_a_folder_that_is_not_a_model(tmp_path, monkeypatch):
    monkeypatch.setenv("QOOL_DATA_DIR", str(tmp_path / "data"))
    bad = tmp_path / "pictures"
    bad.mkdir()
    (bad / "cat.jpg").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="مش نموذج صالح"):
        M.import_model(bad)


def test_transcription_needs_no_network(tmp_path, monkeypatch):
    """النموذج بعد ما ينزل بيشتغل أوفلاين — ده جوهر الحل على أجهزة الحجب."""
    monkeypatch.setenv("QOOL_DATA_DIR", str(tmp_path / "data"))
    _fake_model(M.model_path("small"))
    (M.model_path("small") / M._COMPLETE_MARK).write_text("ok", encoding="utf-8")

    def no_network(*a, **k):
        raise AssertionError("ما ينفعش يتصل بالشبكة")

    monkeypatch.setattr(socket, "create_connection", no_network)
    assert M.is_downloaded("small")
    assert M.is_valid_model_dir(M.model_path("small"))


def test_blocked_primary_falls_back_to_mirror_by_itself(tmp_path, monkeypatch):
    """لازم يجرّب المرايا لوحده من غير ما المستخدم يضبط متغيّر بيئة."""
    monkeypatch.setenv("QOOL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("QOOL_MODEL_ENDPOINT", raising=False)
    tried = []

    def fake_urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        tried.append(url)
        if M.DEFAULT_ENDPOINT in url:
            raise urllib.error.URLError(ConnectionResetError(10054, "blocked"))

        class R:
            status = 200
            def __init__(self):
                self._n = 0
            def read(self, *a):
                if "/api/" in url:
                    return json.dumps([{"type": "file", "path": n, "size": 4}
                                       for n in M.REQUIRED_FILES]).encode()
                self._n += 1
                return b"abcd" if self._n == 1 else b""
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return R()

    with patch("urllib.request.urlopen", fake_urlopen):
        M.download("small")
    assert any(M.DEFAULT_ENDPOINT in u for u in tried), "لازم يبدأ بالمصدر الأساسي"
    assert any(M.MIRRORS[0] in u for u in tried), "لازم يجرّب المرآة لوحده"
    assert M.is_downloaded("small")


def test_explicit_endpoint_disables_the_mirrors(monkeypatch):
    monkeypatch.setenv("QOOL_MODEL_ENDPOINT", "https://models.jci.local")
    assert M.sources() == ["https://models.jci.local"]


def test_all_sources_blocked_raises_the_network_error(tmp_path, monkeypatch):
    monkeypatch.setenv("QOOL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("QOOL_MODEL_ENDPOINT", raising=False)

    def always_blocked(req, timeout=None):
        raise urllib.error.URLError(ConnectionResetError(10054, "blocked"))

    with patch("urllib.request.urlopen", always_blocked):
        with pytest.raises(urllib.error.URLError):
            M.download("small")
