"""عزل الاختبارات عن بيانات المستخدم.

أي اختبار بينادي Settings.save() كان بيكتب فوق data/settings.json الحقيقي ويمسح
المفاتيح والإعدادات. هنا بنحوّل مجلد البيانات لمجلد مؤقت قبل أي اختبار.
"""
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="qool-tests-")
os.environ["QOOL_DATA_DIR"] = _TMP          # قبل أي import لـ qool.paths
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """مجلد بيانات خاص بكل اختبار، فلا يتسرّب شيء بين الاختبارات ولا لبيانات المستخدم."""
    monkeypatch.setenv("QOOL_DATA_DIR", str(tmp_path))
    yield


def test_user_settings_are_never_touched():
    """حارس: لو رجع مسار البيانات لمجلد المستخدم، الاختبار ده بيقع."""
    from qool.paths import data_dir, settings_file

    assert "qool-tests-" in str(data_dir()) or "pytest" in str(data_dir()).lower()
    assert "Apps" not in str(settings_file()) or "pytest" in str(settings_file()).lower()
