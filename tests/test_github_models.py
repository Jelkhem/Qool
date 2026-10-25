"""التنزيل من GitHub Releases: لمّ الأجزاء، التحقق من البصمة، والرجوع إليه عند الحجب."""
import hashlib
import io
import json
import urllib.error
import zipfile
from unittest.mock import patch

import pytest

from qool import github_models as G
from qool import models as M

PART_A, PART_B = b"first-half-" * 100, b"second-half" * 100


def _extras_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("config.json", "{}")
        z.writestr("tokenizer.json", "{}")
    return buf.getvalue()


def _manifest(extras: bytes) -> dict:
    def d(b):
        return {"size": len(b), "sha256": hashlib.sha256(b).hexdigest()}
    return {"demo": {"label": "demo", "size_mb": 1,
                     "parts": [{"name": "demo.model.bin.part0", **d(PART_A)},
                               {"name": "demo.model.bin.part1", **d(PART_B)}],
                     "extras": {"name": "demo.extras.zip", **d(extras)}}}


class Resp(io.BytesIO):
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _server(extras: bytes, corrupt: bool = False):
    body = {"models.json": json.dumps(_manifest(extras)).encode(),
            "demo.model.bin.part0": PART_A,
            "demo.model.bin.part1": b"WRONG" if corrupt else PART_B,
            "demo.extras.zip": extras}

    def fake(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        name = url.rsplit("/", 1)[-1]
        if name not in body:
            raise urllib.error.HTTPError(url, 404, "no", {}, None)
        return Resp(body[name])
    return fake


def test_downloads_and_joins_parts(tmp_path):
    extras = _extras_zip()
    with patch("urllib.request.urlopen", _server(extras)):
        G.download("demo", tmp_path / "demo")
    out = tmp_path / "demo"
    assert (out / "model.bin").read_bytes() == PART_A + PART_B
    assert (out / "config.json").exists() and (out / "tokenizer.json").exists()
    assert not (out / ".parts").exists()          # المؤقتات اتمسحت


def test_rejects_a_corrupted_part(tmp_path):
    extras = _extras_zip()
    with patch("urllib.request.urlopen", _server(extras, corrupt=True)):
        with pytest.raises(IOError, match="حجم|بصمة"):
            G.download("demo", tmp_path / "demo")
    assert not (tmp_path / "demo" / "model.bin").exists()


def test_available_is_empty_when_unreachable():
    def blocked(*a, **k):
        raise urllib.error.URLError("blocked")
    with patch("urllib.request.urlopen", blocked):
        assert G.available() == []


def test_blocked_huggingface_falls_back_to_github(tmp_path, monkeypatch):
    """ده جوهر الحل لأجهزة الشركة: HF محجوب، فينزّل من GitHub لوحده."""
    monkeypatch.setenv("QOOL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("QOOL_MODEL_ENDPOINT", raising=False)
    used = []

    def fake_github(key, dest, progress=None, cancel=None):
        used.append(key)
        dest.mkdir(parents=True, exist_ok=True)
        for name in M.REQUIRED_FILES:
            (dest / name).write_text("x", encoding="utf-8")
        return dest

    def blocked(*a, **k):
        raise urllib.error.URLError(ConnectionResetError(10054, "blocked"))

    monkeypatch.setattr(G, "download", fake_github)
    with patch("urllib.request.urlopen", blocked):
        M.download("large-v3-turbo")
    assert used == ["large-v3-turbo"]
    assert M.is_downloaded("large-v3-turbo")
