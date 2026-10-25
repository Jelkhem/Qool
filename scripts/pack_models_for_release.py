"""يجهّز ملفات النماذج للرفع على GitHub Releases (حد الملف الواحد ٢ جيجا).

  .venv\\Scripts\\python.exe scripts\\pack_models_for_release.py OUT_DIR [large-v3-turbo large-v3]

بيطلع: model.bin (أو أجزاء لو أكبر من الحد) + extras.zip للملفات الصغيرة + models.json
فيه أحجام وبصمات SHA-256 عشان البرنامج يتأكد إن التنزيل سليم.
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qool.models import CATALOG, REQUIRED_FILES, model_path  # noqa: E402

PART_MB = 1400          # أقل من حد GitHub (٢ جيجا) بهامش مريح
CHUNK = 8 << 20


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def split(src: Path, out_dir: Path, stem: str) -> list[dict]:
    """يقسّم الملف الكبير لأجزاء يقبلها GitHub، ويرجّع وصف كل جزء."""
    limit = PART_MB * 2**20
    size = src.stat().st_size
    if size <= limit:
        dest = out_dir / f"{stem}.model.bin"
        if not dest.exists() or dest.stat().st_size != size:
            dest.write_bytes(src.read_bytes()) if size < (1 << 30) else _copy(src, dest)
        return [{"name": dest.name, "size": size, "sha256": sha256(dest)}]
    parts, index = [], 0
    with open(src, "rb") as f:
        while True:
            dest = out_dir / f"{stem}.model.bin.part{index}"
            written = 0
            with open(dest, "wb") as out:
                while written < limit:
                    block = f.read(min(CHUNK, limit - written))
                    if not block:
                        break
                    out.write(block)
                    written += len(block)
            if written == 0:
                dest.unlink()
                break
            parts.append({"name": dest.name, "size": written, "sha256": sha256(dest)})
            print(f"   جزء {index}: {written/2**20:.0f} MB")
            index += 1
            if written < limit:
                break
    return parts


def _copy(src: Path, dest: Path) -> None:
    with open(src, "rb") as f, open(dest, "wb") as out:
        for block in iter(lambda: f.read(CHUNK), b""):
            out.write(block)


def main() -> int:
    out_dir = Path(sys.argv[1])
    keys = sys.argv[2:] or ["large-v3-turbo", "large-v3"]
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}
    for key in keys:
        src_dir = model_path(key)
        if not all((src_dir / f).exists() for f in REQUIRED_FILES):
            print(f"⚠️  {key}: مش منزَّل على الجهاز — تخطّيته")
            continue
        print(f"\n📦 {key}")
        parts = split(src_dir / "model.bin", out_dir, key)
        extras = out_dir / f"{key}.extras.zip"
        with zipfile.ZipFile(extras, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(src_dir.iterdir()):
                if f.is_file() and f.name not in ("model.bin", ".complete") and not f.name.endswith(".part"):
                    z.write(f, f.name)
                    print(f"   في extras: {f.name}")
        manifest[key] = {
            "label": CATALOG[key].label if key in CATALOG else key,
            "size_mb": CATALOG[key].size_mb if key in CATALOG else 0,
            "parts": parts,
            "extras": {"name": extras.name, "size": extras.stat().st_size, "sha256": sha256(extras)},
        }
    (out_dir / "models.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(f.stat().st_size for f in out_dir.iterdir() if f.is_file())
    print(f"\n✅ جاهز في: {out_dir}")
    print(f"   {len(list(out_dir.iterdir()))} ملف، الإجمالي {total/2**30:.2f} جيجا")
    print("   ارفعهم كلهم (ومعاهم models.json) في GitHub Release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
