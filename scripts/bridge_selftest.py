"""اختبار جسر الميتينج على الجهاز الحقيقي (من غير Teams):

عملية جانبية بتشغّل نغمة هادية على جهاز الخرج الافتراضي ← الجسر بيكتشف الجهاز ده من جلسات الصوت
← endpoint loopback ← «CABLE Input» ← بنقيس على «CABLE Output».

⚠ متشغلوش وإنت في ميتينج والجسر شغال في «قول»: النغمة هتروح لـChatGPT.

  .venv\\Scripts\\python.exe scripts\\bridge_selftest.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402

from qool import meeting_bridge as mb  # noqa: E402

# العملية الجانبية بتطبع سطر لما النغمة تبدأ فعلًا (بدء Python ممكن ياخد ثواني لو الـantivirus بيفحص)
TONE = ("import numpy as n,sounddevice as s;t=n.arange(int(48000*8))/48000;"
        "s.play((0.03*n.sin(2*n.pi*440*t)).astype('float32'),48000);print('playing',flush=True);s.wait()")


def db(v: float) -> float:
    return 20 * np.log10(max(v, 1e-6))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cable_out = next((i, n) for i, n in mb._wasapi_devices("input") if "CABLE Output" in n)
    child = subprocess.Popen([sys.executable, "-c", TONE], stdout=subprocess.PIPE, text=True)
    child.stdout.readline()
    time.sleep(0.5)
    levels: list[float] = []
    try:
        exe = Path(sys.executable).name
        bridge = mb.MeetingBridge(app_exe=exe, ai_exe="no-ai-app.exe", mic_gain=0.0)  # المايك مكتوم
        bridge.start()
        print(f"الجهاز اللي اتكشف: {bridge.device_label}")
        with sd.InputStream(device=cable_out[0], samplerate=mb.RATE, channels=1, dtype="float32",
                            extra_settings=sd.WasapiSettings(auto_convert=True),
                            callback=lambda d, f, t, s: levels.append(float(np.sqrt(np.mean(d * d))))):
            time.sleep(3.0)
        app_level = bridge.app_ring.level
        problem = bridge.problem()
        bridge.stop()
    finally:
        child.kill()

    cable = float(np.median(levels[len(levels) // 3:])) if levels else 0.0
    ok = db(app_level) > -45 and db(cable) > -45 and problem is None
    print(f"التقاط جهاز الخرج : {db(app_level):6.1f} dBFS")
    print(f"وصل للكابل         : {db(cable):6.1f} dBFS")
    print(f"مشاكل              : {problem}")
    print("النتيجة: نجح ✅" if ok else "النتيجة: فشل ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
