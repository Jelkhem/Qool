"""يسجّل من المايك مرة واحدة ويفرّغ نفس الصوت بكل الطرق، عشان المقارنة تبقى عادلة.

  .venv\\Scripts\\python.exe scripts\\compare_stt.py [ثواني]

لا يحفظ أي ملف صوت؛ الصوت في الذاكرة فقط ويتمسح بعد الانتهاء.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qool import groq_stt                      # noqa: E402
from qool.audio import Recorder                # noqa: E402
from qool.cleanup import clean_text, join_segments  # noqa: E402
from qool.config import DEFAULT_PROMPT, Settings  # noqa: E402
from qool.pipeline import resolve_model        # noqa: E402
from qool.transcriber import Transcriber       # noqa: E402

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
s = Settings.load()
prompt = " ".join(x for x in (s.initial_prompt, s.vocabulary) if x.strip())

rec = Recorder()
rec.start(s.microphone)
print(f"\n🎤 اتكلم دلوقتي… ({seconds:.0f} ثانية)")
for left in range(int(seconds), 0, -1):
    print(f"   {left} ", end="\r", flush=True)
    time.sleep(1)
audio = rec.stop()
print(f"\n✅ اتسجّل {len(audio) / 16000:.1f} ثانية.")


def _db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9))


def mic_report(a: np.ndarray) -> None:
    """فحص سريع للمايك: المستوى، القص، ونسبة الكلام للدوشة. المشاكل دي بتخلي النص يطلع غلط."""
    frame = 1600  # 0.1 ث
    n = len(a) // frame * frame
    rms = np.sqrt((a[:n].reshape(-1, frame) ** 2).mean(axis=1)) if n else np.array([0.0])
    # مئويات بدل عتبة ثابتة: مع دوشة متواصلة مفيش لحظات «سكوت» فالعتبة بتدي أرقامًا وهمية
    peak_db = _db(np.abs(a).max())
    speech_db, noise_db = _db(np.percentile(rms, 90)), _db(np.percentile(rms, 10))
    clipped = float((np.abs(a) > 0.99).mean() * 100)
    print(f"\n🎚️ فحص المايك: أعلى مستوى {peak_db:.0f} dB | متوسط الكلام {speech_db:.0f} dB | "
          f"الدوشة {noise_db:.0f} dB | فرق الكلام عن الدوشة {speech_db - noise_db:.0f} dB | قص {clipped:.1f}%")
    if speech_db < -36:
        print("   ⚠️ صوتك واطي قوي — قرّب المايك أو ارفع مستواه من إعدادات الصوت في ويندوز.")
    if speech_db - noise_db < 20:
        print("   ⚠️ الدوشة عالية مقارنة بصوتك — ده أكتر سبب بيخلي التفريغ يطلع كلام غلط.")
    if clipped > 0.5:
        print("   ⚠️ الصوت بيتقص (عالي أوي) — قلّل مستوى المايك شوية.")
    if speech_db >= -36 and speech_db - noise_db >= 20 and clipped <= 0.5:
        print("   ✅ المايك كويس.")


mic_report(audio)
print("\nبنفرّغ…\n")

results: list[tuple[str, float, str]] = []

PROMPTS = {"سياقك الحالي": prompt,
           "سياق مصري أقوى": " ".join(x for x in (DEFAULT_PROMPT, s.vocabulary) if x.strip()),
           "بدون سياق": ""}

if s.groq_key():
    for model in groq_stt.MODELS:
        for pname, ptext in PROMPTS.items():
            try:
                segs, took = groq_stt.transcribe(audio, api_key=s.groq_key(), model=model, prompt=ptext)
                results.append((f"أونلاين — {model} | {pname}", took,
                                clean_text(join_segments(segs, s.paragraph_gap_seconds))))
            except groq_stt.GroqError as e:
                results.append((f"أونلاين — {model} | {pname}", 0.0, f"فشل: {e}"))
else:
    print("(مفيش مفتاح Groq محفوظ — هنجرب المحلي بس)")

t = Transcriber()
for name in (s.model, "large-v3"):
    try:
        path, key, need = resolve_model(Settings(model=name))
    except Exception as e:
        results.append((f"محلي — {name}", 0.0, f"مش متاح: {e}"))
        continue
    t.ensure_loaded(path, key, s.device, s.gpu_compute_type, s.cpu_compute_type, need)
    start = time.perf_counter()
    res = t.transcribe(audio, language="ar", initial_prompt=s.initial_prompt,
                       hotwords=s.vocabulary, beam_size=s.beam_size)
    results.append((f"محلي — {key} ({t.info.device})", time.perf_counter() - start,
                    clean_text(join_segments(res.segments, s.paragraph_gap_seconds))))
    if name == s.model and s.model == "large-v3":
        break
t.unload()

print("=" * 70)
for name, took, text in results:
    print(f"\n▶ {name}   [{took:.1f} ث]\n{text}")
print("\n" + "=" * 70)
print("قارن النصوص فوق وشوف أي واحد أقرب للي قلته بالظبط.")
