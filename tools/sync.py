#!/usr/bin/env python3
"""Refresh this repository's copies of the LZBank files from the NextBuild tree.

    python/bin/python Sources/LZBank/tools/sync.py           copy the current files in
    python/bin/python Sources/LZBank/tools/sync.py --check   only report what differs

NextBuild holds the working copies (Scripts/, Tools/). The repository
carries the same files at the same relative paths, so a clone can be copied
straight over a NextBuild folder, and the README's paths work in both.
"""
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parents[1]

FILES = [
    "Scripts/nextlib_lzbank.bas",
    "Scripts/tools/lzbank.py",
    "Tools/lzbank",
    "Tools/lzbank.exe",
    "Tools/lzbank-macos",
    "Tools/lzbank-linux-arm64",
    "Tools/lzbank-linux-arm",
    "Tools/src/lzbank/lzbank.c",
    "Tools/src/lzbank/build.sh",
    "Tools/src/lzbank/build.bat",
    "Tools/src/lzbank/lipo.py",
]


def stale():
    """the files whose repository copy is missing or differs from NextBuild's"""
    return [f for f in FILES
            if not (PROJECT / f).exists() or (PROJECT / f).read_bytes() != (ROOT / f).read_bytes()]


def main():
    out = stale()
    if "--check" in sys.argv:
        for f in out:
            print(f"differs: {f}")
        return 1 if out else 0
    for f in out:
        dst = PROJECT / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dst)
        print(f"copied {f}")
    if not out:
        print("all current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
