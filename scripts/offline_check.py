"""يتحقق أن الإملاء لا يستخدم الشبكة إطلاقًا، ويختبر fallback الـCPU عند نقص VRAM أو خطأ CUDA.

  .venv\\Scripts\\python.exe scripts\\offline_check.py path\\to\\speech.wav
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

attempts = []


def _blocked(*a, **k):
    attempts.append(a[:2])
    raise OSError("الشبكة ممنوعة في هذا الاختبار")


socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.getaddrinfo = _blocked
socket.create_connection = _blocked

from faster_whisper import decode_audio  # noqa: E402

from qool.config import Settings  # noqa: E402
from qool.pipeline import Pipeline, resolve_model  # noqa: E402
from qool.transcriber import Transcriber  # noqa: E402

audio = decode_audio(sys.argv[1])
s = Settings()

# 1) إملاء كامل دون شبكة
p = Pipeline(s)
out = p.process_audio(audio)
print(f"[1] دون إنترنت: جهاز={out.info['device']} معالجة={out.info['process_seconds']}ث كلمات={len(out.clean_text.split())} "
      f"محاولات اتصال={len(attempts)}")
assert not attempts and out.clean_text

# 2) ذاكرة كارت غير كافية وقت التحميل -> CPU مع سبب واضح
path, name, _ = resolve_model(s)
t = Transcriber()
info = t.ensure_loaded(path, name, "auto", "int8_float16", "int8", vram_needed_mb=10**7)
print(f"[2] VRAM غير كافية: جهاز={info.device} السبب={info.fallback_reason}")
assert info.device == "cpu" and info.fallback_reason
t.unload()

# 3) خطأ نفاد ذاكرة CUDA أثناء التفريغ -> إعادة المحاولة على CPU دون فقد التسجيل
p2 = Pipeline(Settings())
p2.load()
orig = p2.transcriber._run
calls = {"n": 0}


def flaky(*a, **k):
    calls["n"] += 1
    if p2.transcriber.info.device == "cuda":
        raise RuntimeError("CUDA failed with error out of memory")
    return orig(*a, **k)


p2.transcriber._run = flaky
out2 = p2.process_audio(audio)
print(f"[3] OOM أثناء التفريغ: جهاز={out2.info['device']} fallback={out2.info['fallback']} محاولات={calls['n']} "
      f"كلمات={len(out2.clean_text.split())}")
assert out2.info["device"] == "cpu" and out2.info["fallback"] and out2.clean_text
print("كل فحوص عدم الاتصال والـfallback نجحت. محاولات اتصال بالشبكة:", len(attempts))
