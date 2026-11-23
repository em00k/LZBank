#!/usr/bin/env python3
"""Build LZBankAt's data: six 320x256 pictures as a bundle of six streams.

    python/bin/python Sources/LZBank/tools/make_slides.py

Each .nxi (81920 bytes of pixels + 512 of palette) is packed on its own, so
the program can unpack any one of them with LZDepackBankAt. The bundle
starts with a table: [count][offset of each stream] as words. The same
file comes from the command line with
    python Scripts/tools/lzbank.py -b <the six .nxi> -o data/slides.lzb
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
ROOT = PROJECT.parents[1]
sys.path.insert(0, str(ROOT / "Scripts" / "tools"))
import lzbank                                                   # noqa: E402

ASSETS = ROOT / "Sources" / "ReadIFF" / "assets"
SLIDES = [
    "DMADesign_1990_Lineart-320x256.nxi",
    "Team7_1990-320x256.nxi",
    "GremlinGraphics_1989-320x256.nxi",
    "Psygnosis_1990_b-320x256.nxi",
    "Hewson_1991a-320x256.nxi",
    "ShadowOfTheBeast2_OtherWorld-320x256.nxi",
]


def files():
    return [(ASSETS / name).read_bytes() for name in SLIDES]


def main():
    out, offsets, streams = lzbank.bundle(files())
    dst = PROJECT / "data" / "slides.lzb"
    dst.write_bytes(out)
    for k, (name, o, st) in enumerate(zip(SLIDES, offsets, streams)):
        print(f"  {k}  offset {o:5}  {len(st):5} bytes  {name}")
    print(f"{len(out)} bytes, {(len(out) + 8191) // 8192} pages -> {dst}")


if __name__ == "__main__":
    main()
