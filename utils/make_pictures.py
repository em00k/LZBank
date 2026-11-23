#!/usr/bin/env python3
"""Build the demo's data: six 320x256 pictures as one stream, packed.

    python/bin/python Sources/LZBank/utils/make_pictures.py

Each picture is a ReadIFF .nxi (81920 bytes of pixels + 512 of palette),
padded to 12 pages (96K) so that every one starts on an even page, which is
what NextReg $12 needs to show it where it was unpacked. Six of them are
576K, written to data/pictures.lzb by Scripts/tools/lzbank.py.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
ROOT = PROJECT.parents[1]
sys.path.insert(0, str(ROOT / "Scripts" / "tools"))
import lzbank                                                   # noqa: E402

ASSETS = ROOT / "Sources" / "ReadIFF" / "assets"
PICTURES = [
    "Team7_1990-320x256.nxi",
    "Hewson_1991a-320x256.nxi",
    "Psygnosis_1990_b-320x256.nxi",
    "Stormlord_atarist-320x256.nxi",
    "GremlinGraphics_1989-320x256.nxi",
    "ShadowOfTheBeast2_OtherWorld-320x256.nxi",
]
PIC_PAGES = 12


def build():
    out = bytearray()
    for name in PICTURES:
        d = (ASSETS / name).read_bytes()
        assert len(d) == 81920 + 512, f"{name}: no palette"
        out += d + bytes(PIC_PAGES * 8192 - len(d))
    return bytes(out)


def main():
    raw = build()
    packed = lzbank.pack(raw, progress=lambda i, n, t: print(f"\r  {i * 100 // n}%  {t:.0f}s", end="",
                                                             file=sys.stderr, flush=True))
    print("\r" + " " * 20 + "\r", end="", file=sys.stderr)
    dst = PROJECT / "data" / "pictures.lzb"
    dst.write_bytes(packed)
    print(f"{len(raw)} -> {len(packed)} bytes, {(len(packed) + 8191) // 8192} pages -> {dst}")


if __name__ == "__main__":
    main()
