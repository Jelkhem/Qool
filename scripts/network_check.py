"""فحص الشبكة على أجهزة الشركة (Zscaler وغيره): إيه المسموح وإيه المحجوب.

  .venv\\Scripts\\python.exe scripts\\network_check.py
  أو بدون بيئة افتراضية:  python\\python.exe scripts\\network_check.py

بيفرّق بين تلات حالات مهمة لأن علاج كل واحدة مختلف:
  • محجوب   : البوابة بترفض الطلب (صفحة حظر أو إغلاق الاتصال)
  • معترَض  : الاتصال شغال لكن الشهادة من Zscaler، فبايثون بيرفضها
  • شغال    : تمام
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TARGETS = [
    ("تنزيل النماذج", "huggingface.co", "https://huggingface.co/api/models/Systran/faster-whisper-large-v3-turbo"),
    ("ملفات النماذج", "cdn-lfs.hf.co", "https://cdn-lfs.hf.co/"),
    ("تثبيت المكتبات", "pypi.org", "https://pypi.org/simple/numpy/"),
    ("ملفات المكتبات", "files.pythonhosted.org", "https://files.pythonhosted.org/"),
    ("التفريغ الأونلاين", "api.groq.com", "https://api.groq.com/openai/v1/models"),
    ("القارئ (Gemini)", "generativelanguage.googleapis.com",
     "https://generativelanguage.googleapis.com/v1beta/models"),
    ("تحديث البرنامج", "github.com", "https://github.com/Jelkhem/Qool"),
]

UA = "Qool/1.0 (network-check)"


def tls_issuer(host: str) -> tuple[str, str]:
    """(الحالة، الجهة المصدِرة للشهادة)."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                issuer = dict(x[0] for x in ss.getpeercert()["issuer"])
                name = issuer.get("organizationName") or issuer.get("commonName") or "?"
                return "ok", name
    except ssl.SSLCertVerificationError as e:
        return "intercepted", str(e)[:70]
    except (socket.gaierror, socket.timeout, ConnectionError, OSError) as e:
        return "blocked", f"{type(e).__name__}: {str(e)[:60]}"


BLOCK_WORDS = ("zscaler", "access denied", "web page blocked", "blocked by", "not permitted",
               "محجوب", "تم حظر")


def http_status(url: str) -> tuple[bool, str]:
    """(واصل؟، وصف).

    أي رد HTTP معناه إن الاتصال وصل للخدمة — حتى 401/403 من API بيطلب مفتاح.
    الحجب الحقيقي بيبان من صفحة حظر في الرد، أو من فشل الاتصال نفسه.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read(400).decode("utf-8", "replace").lower()
            if any(w in body for w in BLOCK_WORDS):
                return False, f"HTTP {r.status} ← صفحة حظر"
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(400).decode("utf-8", "replace").lower()
        except Exception:
            pass
        if any(w in body for w in BLOCK_WORDS):
            return False, f"HTTP {e.code} ← صفحة حظر"
        return True, f"HTTP {e.code} (رد طبيعي من الخدمة)"
    except urllib.error.URLError as e:
        return False, f"فشل: {str(e.reason)[:50]}"
    except Exception as e:
        return False, f"فشل: {type(e).__name__}"


def main() -> int:
    print("فحص الشبكة لبرنامج قول\n" + "=" * 78)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "(مفيش)"
    print(f"البروكسي في متغيرات البيئة: {proxy}")
    try:
        import certifi
        print(f"ملف الشهادات: {certifi.where()}")
    except Exception:
        print("ملف الشهادات: certifi مش متثبتة")
    print("=" * 78)
    print(f"{'الخدمة':<22}{'العنوان':<36}{'الحالة'}")
    print("-" * 78)
    problems = []
    for label, host, url in TARGETS:
        state, detail = tls_issuer(host)
        if state == "ok":
            reachable, status = http_status(url)
            if not reachable:
                problems.append((label, "محجوب من البوابة"))
            print(f"{label:<22}{host:<36}{'✅' if reachable else '⛔'} {status} | شهادة: {detail}")
        elif state == "intercepted":
            problems.append((label, "اعتراض شهادات (Zscaler)"))
            print(f"{label:<22}{host:<36}⛔ شهادة مرفوضة — {detail}")
        else:
            problems.append((label, "محجوب / مش واصل"))
            print(f"{label:<22}{host:<36}⛔ {detail}")
    print("=" * 78)
    if not problems:
        print("كل حاجة شغالة ✅")
        return 0
    print("الخلاصة:")
    for label, why in problems:
        print(f"  • {label}: {why}")
    if any("اعتراض" in w for _, w in problems):
        print("\nعلاج الاعتراض: شغّل البرنامج بشهادات ويندوز:")
        print('  set QOOL_USE_SYSTEM_CERTS=1   (أو ثبّت الحزمة truststore)')
    if any("محجوب" in w for _, w in problems):
        print("\nعلاج الحجب: استخدم النسخة المحمولة (نموذج منزَّل مسبقًا، بلا إنترنت)،")
        print("أو اطلب من IT السماح بالعناوين اللي فوق.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
