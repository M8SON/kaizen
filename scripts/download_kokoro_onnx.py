#!/usr/bin/env python3
"""Download Kokoro ONNX model + voices into ~/.miniclaw/models/kokoro-onnx/.

The kokoro-onnx package needs two files at runtime:
  - kokoro-v1.0.int8.onnx (~30 MB, int8 quantized)
  - voices-v1.0.bin       (~10 MB, voice embeddings)

Both are published on the kokoro-onnx GitHub releases page. This script
fetches them once into the user's miniclaw data dir so KokoroONNXBackend
finds them at startup.

Re-run is idempotent — files that already exist with the right size are
skipped.

Usage:
    python3 scripts/download_kokoro_onnx.py
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

ASSET_ROOT = Path.home() / ".miniclaw" / "models" / "kokoro-onnx"

# kokoro-onnx releases pin model artifacts at this release tag.
# https://github.com/thewh1teagle/kokoro-onnx/releases
_RELEASE_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

ASSETS = [
    {
        "name": "kokoro-v1.0.int8.onnx",
        "url": f"{_RELEASE_BASE}/kokoro-v1.0.int8.onnx",
        # int8 quantized variant — ~30 MB, fastest on Pi 5 ARM64. Full-
        # precision kokoro-v1.0.onnx (~80 MB) is also available if you
        # want max quality and have the CPU headroom.
    },
    {
        "name": "voices-v1.0.bin",
        "url": f"{_RELEASE_BASE}/voices-v1.0.bin",
    },
]


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
    ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    for asset in ASSETS:
        dest = ASSET_ROOT / asset["name"]
        if dest.exists() and dest.stat().st_size > 0:
            print(f"{dest.name} already present ({dest.stat().st_size / 1e6:.1f} MB) — skipping")
            continue
        _download(asset["url"], dest)
    print(f"\nDone. Set TTS_BACKEND=kokoro-onnx in .env to use the ONNX backend.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
