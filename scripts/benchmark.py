"""قياس الأداء الفعلي: زمن تحميل النموذج منفصلًا عن زمن التفريغ، والذاكرة.

الاستخدام:
  .venv\\Scripts\\python.exe scripts\\benchmark.py [--device auto|cuda|cpu] [--model large-v3-turbo] file1.wav ...
  بدون ملفات: يستخدم مجلد FLEURS إن مُرِّر عبر --fleurs DIR (ملفات wav + dev.tsv للمرجع).
لا يحفظ النصوص في أي ملف؛ يطبعها فقط على الشاشة.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import psutil

from qool.cleanup import clean_text, join_segments
from qool.config import Settings
from qool.pipeline import Pipeline, NoSpeech
from qool.transcriber import gpu_memory_mb


def norm(t: str) -> str:
    t = re.sub(r"[ًٌٍَُِّْـ]", "", t)
    t = re.sub(r"[إأآ]", "ا", t).replace("ة", "ه").replace("ى", "ي")
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def wer(ref: str, hyp: str) -> float:
    r, h = norm(ref).split(), norm(hyp).split()
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))


def load_wav(path: str) -> np.ndarray:
    from faster_whisper import decode_audio
    return decode_audio(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--compute", default="int8_float16")
    ap.add_argument("--fleurs", default="")
    ap.add_argument("-n", type=int, default=6)
    ap.add_argument("--long", action="store_true", help="اختبار تسجيل طويل بسكتات طويلة وصمت فقط")
    ap.add_argument("--quiet-text", action="store_true")
    a = ap.parse_args()

    refs = {}
    files = list(a.files)
    if a.fleurs:
        with open(os.path.join(a.fleurs, "dev.tsv"), encoding="utf-8") as f:
            for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                refs[row[1]] = row[2]
        files = sorted(glob.glob(os.path.join(a.fleurs, "dev", "*.wav")))[: a.n]

    s = Settings(model=a.model, device=a.device, gpu_compute_type=a.compute)
    proc = psutil.Process()
    print(f"RAM العملية قبل التحميل: {proc.memory_info().rss / 2**20:.0f} MB | GPU (الجهاز كله) مستخدم/متاح: {gpu_memory_mb()}")
    p = Pipeline(s)
    info = p.load()
    print(f"النموذج={info.model_name} الجهاز={info.device} الدقة={info.compute_type} زمن التحميل={info.load_seconds:.2f} ث "
          f"{'fallback: ' + info.fallback_reason if info.fallback_reason else ''}")
    print(f"RAM العملية بعد التحميل: {proc.memory_info().rss / 2**20:.0f} MB | GPU (الجهاز كله) مستخدم/متاح: {gpu_memory_mb()}")

    # إحماء (أول تفريغ يتضمن تهيئة kernels) — يُقاس منفصلًا
    audios = [(f, load_wav(f)) for f in files]
    if audios:
        t = time.perf_counter()
        p.transcriber.transcribe(audios[0][1][:16000 * 3], beam_size=s.beam_size)
        print(f"إحماء أول تفريغ (3 ث): {time.perf_counter() - t:.2f} ث")

    total_audio = total_proc = 0.0
    wers = []
    for f, audio in audios:
        out = p.process_audio(audio)
        i = out.info
        total_audio += i["audio_seconds"]
        total_proc += i["process_seconds"]
        line = f"{os.path.basename(f)}: صوت {i['audio_seconds']} ث، معالجة {i['process_seconds']} ث"
        ref = refs.get(os.path.basename(f))
        if ref:
            w = wer(ref, out.raw_text)
            wers.append(w)
            line += f"، WER {w:.0%}"
        print(line)
        if not a.quiet_text:
            print("   خام   :", out.raw_text)
            print("   منظّف :", out.clean_text)
            if ref:
                print("   مرجع   :", ref)
    if total_audio:
        print(f"الإجمالي: صوت {total_audio:.1f} ث، معالجة {total_proc:.2f} ث، RTF={total_proc / total_audio:.3f}"
              + (f"، متوسط WER={sum(wers) / len(wers):.0%}" if wers else ""))
    print(f"ذروة RAM للعملية: {proc.memory_info().peak_wset / 2**20:.0f} MB | GPU (الجهاز كله) مستخدم/متاح الآن: {gpu_memory_mb()}")

    if a.long and audios:
        sr = 16000
        # صمت فقط (مع ضوضاء خفيفة جدًا)
        silence = (np.random.randn(sr * 8) * 0.0015).astype(np.float32)
        try:
            out = p.process_audio(silence)
            print("صمت فقط: أنتج نصًا (غير متوقع):", out.clean_text)
        except NoSpeech as e:
            print("صمت فقط: لم يُكتب نص ✓ —", e)
        # تسجيل طويل: مقاطع مع سكتات 12 ثانية بينها حتى ~3 دقائق
        parts = []
        while sum(len(x) for x in parts) < sr * 180:
            for _, au in audios:
                parts.append(au)
                parts.append((np.random.randn(sr * 12) * 0.001).astype(np.float32))
        long_audio = np.concatenate(parts)[: sr * 180]
        t = time.perf_counter()
        out = p.process_audio(long_audio)
        print(f"تسجيل طويل {len(long_audio) / sr:.0f} ث بسكتات 12 ث: معالجة {time.perf_counter() - t:.2f} ث، "
              f"كلام {out.info['speech_seconds']} ث، أسطر={out.clean_text.count(chr(10)) + 1}، كلمات={len(out.clean_text.split())}")
        print(f"ذروة RAM للعملية بعد الطويل: {proc.memory_info().peak_wset / 2**20:.0f} MB | GPU: {gpu_memory_mb()}")


if __name__ == "__main__":
    main()
