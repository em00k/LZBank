#!/usr/bin/env python3
"""LZBank off hardware: the packer and the Next's depacker, byte for byte.

    python/bin/python Sources/LZBank/tests/test_lzbank.py

Builds the demo (the depacker is in CODEBANK 1, so every call here also goes
through the far-call trampoline), packs test data with Scripts/tools/lzbank.py,
puts it in pages and calls LZDepackBank / LZDepackBankAt in the Z80 model.
Each case checks the output pages against the original, that nothing before
or after the output changed, that the packed pages are untouched, and that
MMU slots 0-2 are back as they were.
"""
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
ROOT = PROJECT.parents[1]
sys.path.insert(0, str(ROOT / "Sources" / "Tests"))
sys.path.insert(0, str(ROOT / "Scripts" / "tools"))
sys.path.insert(0, str(PROJECT / "tools"))
from _next import Next, build_project, Checks                  # noqa: E402
import lzbank                                                   # noqa: E402
import make_pictures                                            # noqa: E402
import make_slides                                              # noqa: E402
import sync                                                     # noqa: E402

PAGE = 8192
FRAME = 560_000                                 # T-states in a 50Hz frame at 28MHz
SRC, DST = 160, 64                              # test pages (the demo's own data is at 48)


def put(m, page, offset, data):
    """data into pages from page:offset, the rest of each page left as it was"""
    p, o = divmod(page * PAGE + offset, PAGE)
    k = 0
    while k < len(data):
        n = min(PAGE - o, len(data) - k)
        buf = m.pages.get(p) or m.fresh_page(p)
        buf[o:o + n] = data[k:k + n]
        m.pages[p] = buf
        k += n
        p, o = p + 1, 0


def get(m, page, offset, n):
    out = bytearray()
    p, o = divmod(page * PAGE + offset, PAGE)
    while len(out) < n:
        k = min(PAGE - o, n - len(out))
        out += (m.pages.get(p) or m.fresh_page(p))[o:o + k]
        p, o = p + 1, 0
    return bytes(out)


def snapshot(m, lo, hi):
    return {p: bytes(m.pages.get(p) or m.fresh_page(p)) for p in range(lo, hi)}


def run_case(c, m, lab, name, data, src=SRC, offset=0, packed=None):
    packed = packed or lzbank.pack(data)
    put(m, src, offset, packed)
    out_pages = (len(data) + PAGE - 1) // PAGE
    before = snapshot(m, DST - 1, DST + out_pages + 1)
    src_before = get(m, src, 0, offset + len(packed) + PAGE)
    mmu = (m.slot_page[0], m.slot_page[1], m.win2_page)
    if offset:
        m.call(lab["_LZDepackBankAt"], args=[(src, True), (offset, False), (DST, True)])
    else:
        m.call(lab["_LZDepackBank"], args=[(src, True), (DST, True)])
    ok = c(f"{name}: unpacks byte for byte", get(m, DST, 0, len(data)) == data, True)
    if not ok:
        got = get(m, DST, 0, len(data))
        first = next(i for i in range(len(data)) if got[i] != data[i])
        print(f"       first difference at {first} (page {DST + first // PAGE} +{first % PAGE})")
    end = len(data)
    tail = get(m, DST, end, out_pages * PAGE - end + PAGE)
    want = b"".join(before[p] for p in range(DST, DST + out_pages + 1))[end:]
    if not c(f"{name}: nothing written past the end", tail == want, True):
        diff = [i for i in range(len(tail)) if tail[i] != want[i]]
        print(f"       {len(diff)} bytes differ, first at end+{diff[0]} (page {DST + (end + diff[0]) // PAGE} +{(end + diff[0]) % PAGE}), last end+{diff[-1]}")
    c(f"{name}: page before the output untouched", get(m, DST - 1, 0, PAGE) == before[DST - 1], True)
    c(f"{name}: packed data untouched", get(m, src, 0, len(src_before)) == src_before, True)
    c(f"{name}: slots 0-2 put back", (m.slot_page[0], m.slot_page[1], m.win2_page), mmu)
    return m.last_ticks


def main():
    resident, org, pages, lab = build_project(PROJECT / "LZBank.bas")
    # the stack must stay out of slots 0-3 while the depacker pages them
    m = Next(resident, org=org, pages=pages, dirty=1, stack=0xFDFE)
    c = Checks("LZBank")
    rnd = random.Random(7)

    c.section("repository copies")
    c("Scripts/ and Tools/ here match NextBuild's (tools/sync.py)", sync.stale(), [])

    c.section("packer: the bank rule, and ZX0 v2 compatibility")
    for n in (1, 2, 3, 100, 9000):
        d = bytes(rnd.randrange(4) for _ in range(n))
        p = lzbank.pack(d)
        c(f"{n} bytes round trip, every match inside its page",
          lzbank.decode(p, check_rules=True)[0], d)
    sample = bytes(make_pictures.build()[:PAGE * 3])
    p = lzbank.pack(sample)
    back, used = lzbank.decode(p, check_rules=True)
    c("3 pages of a picture: round trip, the whole stream read", (back == sample, used), (True, len(p)))

    exe = lzbank.native()
    c.section(f"native packer ({exe.name if exe else 'none for this machine: Python only'})")
    if exe:
        blk = rnd.randbytes(5000)
        for name, d, kw in (
                ("picture, 3 pages", sample, {}),
                ("picture, 3 pages, -x", sample, dict(cut=1024, keep=16, nice=256)),
                ("noise", rnd.randbytes(20000), {}),
                ("runs", b"".join(bytes([rnd.randrange(256)]) * rnd.randrange(1, 3000) for _ in range(20)), {}),
                ("far matches", blk + rnd.randbytes(20000) + blk, {}),
                ("1 byte", b"\x00", {})):
            c(f"{name}: the same bytes as the Python", lzbank.pack(d, **kw) == lzbank.pack_python(d, **kw), True)

    c.section("depacker")
    t = {}
    t["small"] = run_case(c, m, lab, "1 byte", b"\x5a")
    run_case(c, m, lab, "3 bytes", b"abc")
    # incompressible: literal runs over packed and output page ends
    noise = rnd.randbytes(3 * PAGE + 123)
    t["noise"] = run_case(c, m, lab, "24K of noise", noise)
    # runs: long matches stopping at every page end, rep matches after them
    runs = b"".join(bytes([rnd.randrange(256)]) * rnd.randrange(1, 3000) for _ in range(40))
    run_case(c, m, lab, "long runs", runs)
    # far matches: a block repeated 20-32K later, its start in another page
    blk = rnd.randbytes(7000)
    far = blk + rnd.randbytes(15000) + blk + rnd.randbytes(9000) + blk + blk[:3000]
    far_packed = lzbank.pack(far)
    run_case(c, m, lab, "far matches", far, packed=far_packed)
    c("far matches: the packer used offsets past 16K", any(
        t[0] == 'N' and t[1] > 16384 for t in lzbank.parse(far)), True)
    # a stream that starts part way into a page and crosses into the next
    run_case(c, m, lab, "LZDepackBankAt, offset 9000", noise[:5000] + runs[:20000], offset=9000)
    run_case(c, m, lab, "LZDepackBankAt, offset 8191", far[:12000], offset=8191)

    c.section("the demo: six pictures, one stream")
    raw = make_pictures.build()
    packed = (PROJECT / "data" / "pictures.lzb").read_bytes()
    c("data/pictures.lzb is the current pictures", lzbank.decode(packed)[0] == raw, True)
    c("it fits before BK_PICS", 48 + (len(packed) + PAGE - 1) // PAGE <= DST, True)
    t["demo"] = run_case(c, m, lab, "576K", raw, src=48, packed=packed)
    c("slot 3 only paged by the far call: the bank, then back",
      {v for r, v in m.nextreg_log if r == 0x53} <= {40, m.win_page}, True)

    c.section("LZBankAt: a bundle, one picture at a time, double buffered")
    resident, org, pages, lab2 = build_project(PROJECT / "LZBankAt.bas")
    m2 = Next(resident, org=org, pages=pages, dirty=2, stack=0xFDFE)
    c("no string heap", "core.__MEM_ALLOC" not in lab2, True)
    pics = make_slides.files()
    bundle_file = (PROJECT / "data" / "slides.lzb").read_bytes()
    offs = [int.from_bytes(bundle_file[2 + 2 * k:4 + 2 * k], "little") for k in range(len(pics))]
    c("data/slides.lzb is the current pictures",
      [lzbank.decode(bundle_file[o:], check_rules=True)[0] for o in offs] == pics, True)
    bundle = get(m2, 48, 0, len(bundle_file))
    c("the NEX carries it at BK_SLIDES", bundle == bundle_file, True)
    count = bundle[0] | bundle[1] << 8
    c("table: six pictures", count, 6)
    c("it fits before buffer B", 48 + (len(bundle) + PAGE - 1) // PAGE <= 64, True)

    def screen(m, page):
        """a picture's 82432 bytes from page on: Layer 2's own pages are in m.l2"""
        if page == 18:
            return bytes(m.l2[:81920]) + get(m, 28, 0, 512)
        return get(m, page, 0, 82432)

    hidden = 64
    for k in range(count + 1):                  # round once, and back to the first
        slide = k % count
        off = bundle[2 + slide * 2] | bundle[3 + slide * 2] << 8
        m2.call(lab2["_LZDepackBankAt"], args=[(48, True), (off, False), (hidden, True)])
        c(f"picture {slide} (offset {off}) into page {hidden}", screen(m2, hidden) == pics[slide], True)
        if k == count - 1:
            t["slide"] = m2.last_ticks
        hidden = 18 if hidden == 64 else 64
    c("the bundle is untouched", get(m2, 48, 0, len(bundle)) == bundle_file, True)

    print(f"\n  576K in {t['demo']:,} T = {t['demo'] / FRAME:.1f} frames at 28MHz "
          f"({t['demo'] / len(raw):.1f} T a byte); noise {t['noise'] / len(noise):.1f} T a byte;"
          f" one picture from the bundle {t['slide'] / FRAME:.1f} frames")
    c.done()


if __name__ == "__main__":
    main()
