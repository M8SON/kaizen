#!/usr/bin/env python3
"""Download Kokoro ONNX model + voices into ~/.kaizen/models/kokoro-onnx/.

The kokoro-onnx package needs the ONNX model and voices files at runtime:
  - kokoro-v1.0.onnx      (~310 MB, fp32 — the new default)
  - voices-v1.0.bin       (~28 MB, voice embeddings)

fp32 is now the default after Pi 5 testing 2026-05-09 showed it ran
~2x faster than int8 on Cortex-A76 — ONNX Runtime's int8 kernels for
ARM64 aren't tuned for the ARMv8.2 DOTPROD instructions, while the
fp32 path uses well-tuned NEON code. The int8 variant
(kokoro-v1.0.int8.onnx, ~88 MB) is still available with `--int8` for
x86_64 hosts where int8 IS faster.

Both are published on the kokoro-onnx GitHub releases page. Re-runs
are idempotent — files that already exist with non-zero size are skipped.

Usage:
    python3 scripts/download_kokoro_onnx.py          # fp32 (default)
    python3 scripts/download_kokoro_onnx.py --int8   # smaller, slower on Pi
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

ASSET_ROOT = Path.home() / ".kaizen" / "models" / "kokoro-onnx"

# kokoro-onnx releases pin model artifacts at this release tag.
# https://github.com/thewh1teagle/kokoro-onnx/releases
_RELEASE_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

VOICES_ASSET = {
    "name": "voices-v1.0.bin",
    "url": f"{_RELEASE_BASE}/voices-v1.0.bin",
}

MODEL_FP32 = {
    "name": "kokoro-v1.0.onnx",
    "url": f"{_RELEASE_BASE}/kokoro-v1.0.onnx",
}

MODEL_INT8 = {
    "name": "kokoro-v1.0.int8.onnx",
    "url": f"{_RELEASE_BASE}/kokoro-v1.0.int8.onnx",
}


def _download(url: str, dest: Path) -> None:
    print(f"Fetching {dest.name}...")
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            written = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
                if total:
                    pct = 100 * written / total
                    print(
                        f"  {written / 1e6:.1f} / {total / 1e6:.1f} MB ({pct:.0f}%)",
                        end="\r",
                        flush=True,
                    )
        os.replace(tmp, dest)
        print(f"  saved to {dest}")
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def main() -> int:
    int8 = "--int8" in sys.argv
    model = MODEL_INT8 if int8 else MODEL_FP32
    assets = [model, VOICES_ASSET]

    ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        dest = ASSET_ROOT / asset["name"]
        if dest.exists() and dest.stat().st_size > 0:
            print(f"{dest.name} already present ({dest.stat().st_size / 1e6:.1f} MB) — skipping")
            continue
        _download(asset["url"], dest)
    variant = "int8" if int8 else "fp32"
    print(
        f"\nDone. Set TTS_BACKEND=kokoro-onnx (and KOKORO_ONNX_VARIANT={variant} "
        f"if not the default fp32) in .env to use the ONNX backend."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
