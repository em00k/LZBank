#!/usr/bin/env python3
"""lzbank: pack a file of any size so the Next can unpack it across 8K banks.

    python Scripts/tools/lzbank.py data/pictures.bin               -> data/pictures.bin.lzb
    python Scripts/tools/lzbank.py data/pictures.bin -o out.lzb
    python Scripts/tools/lzbank.py -d out.lzb -o check.bin         (unpack, to check)
    python Scripts/tools/lzbank.py -b a.nxi b.nxi c.nxi -o set.lzb (a bundle)

On the Next, with the .lzb loaded at the start of a page:

    #include <nextlib_lzbank.bas>
    LoadSDBank("pictures.bin.lzb", 0, 0, 0, 40)
    LZDepackBank(40, 18)             ' packed from page 40 -> unpacked from page 18 on

A bundle is several files packed one after another behind a table of where
each starts, for LZDepackBankAt (see bundle() below):

    n = BankPeekUint(40, 0)                     ' how many
    LZDepackBankAt(40, BankPeekUint(40, 2 + k * 2), 18)   ' unpack number k

The stream is ZX0 v2 (Einar Saukas' format: interlaced Elias gamma codes and
a "repeat the last offset" match), so any ZX0 v2 decoder reads it. What makes
it a bank stream is one rule the parse keeps: no match crosses an 8K boundary
of the output (counted from its start). The Next's depacker can then copy a
match with one LDIR and move to the next output page between tokens.
Literals and the packed data cross pages freely; the depacker handles those.
A file packed by the `zx0` tool breaks the rule, so LZDepackBank can't read it.

Tools/lzbank is the C port (lzbank.exe on Windows, lzbank-macos, and
lzbank-linux-arm64 / lzbank-linux-arm on ARM Linux), with the same options,
and writes the same bytes about 50 times faster: use it. This script runs it
when there is one for the machine, and says so on stderr when there isn't
and it falls back to its own Python (LZBANK_PYTHON=1 uses the Python quietly).
Its source and build scripts are in Tools/src/lzbank/.

The parse is a forward optimal parse over the ZX0 bit costs: a binary-tree
match finder (LZMA's bt2) and a few "arrivals" (cost, last offset) kept per
position, so it can plan repeat-offset matches. It is not ZX0's exhaustive
search: it comes within about 1-2% of zx0's size, in a tenth of the time.
"""
import argparse
import os
import platform
import subprocess
import sys
import tempfile
import time
from array import array
from pathlib import Path

PAGE = 8192
MAX_OFFSET = 32640                           # ZX0's own limit: offset MSB 255
END_MSB = 256                                # the end marker's offset MSB
MAX_LIT = 65535                              # the Z80 decoder counts in BC

# Elias gamma length in bits for 1..65536
EB = [0] * (65537)
for _v in range(1, 65537):
    EB[_v] = 2 * (_v.bit_length() - 1) + 1


def max_offset(i):
    """the furthest back a match starting at output position i may reach"""
    return min(i, MAX_OFFSET)


def page_room(i):
    """bytes from output position i to the end of its 8K page"""
    return PAGE - (i % PAGE)


# ---------------------------------------------------------------------------
#  Bit writer and reader (ZX0 v2, forwards)
# ---------------------------------------------------------------------------

class BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.mask = 0
        self.at = 0
        self.backtrack = False

    def byte(self, v):
        self.out.append(v)

    def bit(self, v):
        if self.backtrack:                  # the first bit of a new offset's length
            if v:                           # rides in bit 0 of its LSB byte
                self.out[-1] |= 1
            self.backtrack = False
            return
        if not self.mask:
            self.mask = 128
            self.at = len(self.out)
            self.out.append(0)
        if v:
            self.out[self.at] |= self.mask
        self.mask >>= 1

    def elias(self, value, invert=False):
        i = 1 << (value.bit_length() - 1)
        while i > 1:
            i >>= 1
            self.bit(0)
            self.bit((not (value & i)) if invert else (value & i))
        self.bit(1)


def encode(data, tokens):
    """tokens: ('L', start, n) | ('R', n) | ('N', offset, n), in order"""
    w = BitWriter()
    first = True
    for t in tokens:
        if t[0] == 'L':
            if not first:
                w.bit(0)
            w.elias(t[2])
            for b in data[t[1]:t[1] + t[2]]:
                w.byte(b)
        elif t[0] == 'R':
            w.bit(0)
            w.elias(t[1])
        else:
            o, n = t[1], t[2]
            w.bit(1)
            w.elias((o - 1) // 128 + 1, invert=True)
            w.byte((127 - (o - 1) % 128) << 1)
            w.backtrack = True
            w.elias(n - 1)
        first = False
    w.bit(1)
    w.elias(END_MSB, invert=True)
    return bytes(w.out)


def decode(packed, check_rules=False):
    """ZX0 v2 decoder. check_rules: also fail on a match that breaks the bank rules."""
    out = bytearray()
    st = [0, 0, 0, False, 0]                # index, mask, value, backtrack, last byte

    def byte():
        v = packed[st[0]]
        st[0] += 1
        st[4] = v
        return v

    def bit():
        if st[3]:
            st[3] = False
            return st[4] & 1
        st[1] >>= 1
        if not st[1]:
            st[1] = 128
            st[2] = byte()
        return 1 if st[2] & st[1] else 0

    def elias(inv=0):
        v = 1
        while not bit():
            v = v << 1 | (bit() ^ inv)
        return v

    def copy(off, n):
        i = len(out)
        if check_rules:
            if off > max_offset(i):
                raise ValueError(f"match at {i} reaches back {off} (limit {max_offset(i)})")
            if n > page_room(i):
                raise ValueError(f"match at {i} length {n} crosses a page end")
        if off > i:
            raise ValueError(f"match at {i} reaches before the start ({off})")
        for _ in range(n):
            out.append(out[-off])

    last = 1
    state = 0                               # 0 literals, 1 last offset, 2 new offset
    while True:
        if state == 0:
            n = elias()
            if check_rules and n > MAX_LIT:
                raise ValueError(f"literals at {len(out)}: {n} of them (limit {MAX_LIT})")
            out += packed[st[0]:st[0] + n]
            st[0] += n
            state = 2 if bit() else 1
        elif state == 1:
            copy(last, elias())
            state = 2 if bit() else 0
        else:
            msb = elias(1)
            if msb == END_MSB:
                return bytes(out), st[0]
            last = msb * 128 - (byte() >> 1)
            st[3] = True
            copy(last, elias() + 1)
            state = 2 if bit() else 0


# ---------------------------------------------------------------------------
#  The parse
# ---------------------------------------------------------------------------

def _match_len(data, j, i, lim):
    """how many bytes from j equal those from i, up to lim (j < i; may overlap)"""
    lo, step = 0, 8
    while True:
        hi = min(lo + step, lim)
        if data[j + lo:j + hi] != data[i + lo:i + hi]:
            break
        lo = hi
        if lo == lim:
            return lim
        step <<= 1
    while hi - lo > 8:                      # the first difference is in [lo, hi)
        mid = (lo + hi) >> 1
        if data[j + lo:j + mid] == data[i + lo:i + mid]:
            lo = mid
        else:
            hi = mid
    while data[j + lo] == data[i + lo]:
        lo += 1
    return lo


# token codes in the back-pointer store
T_LIT, T_REP, T_NEW = 0, 1, 2
K_LIT, K_MATCH, K_START = 0, 1, 2
TREE_LEN = 256                                  # the match tree compares this far


def match_tree(data, cut):
    """LZMA's bt2 match finder over the ZX0 window.

    Returns insert(i): adds position i (every position must go in, in order)
    and returns its matches as [(offset, length)], each longer and further
    back than the one before, lengths capped at TREE_LEN.
    """
    n = len(data)
    son = array('i', [-1]) * (2 * n)             # per position: smaller, larger subtree
    head = {}

    def insert(i):
        lim = min(TREE_LEN, n - i)
        if lim < 2:
            return []
        h = data[i] | data[i + 1] << 8
        cur = head.get(h, -1)
        head[h] = i
        p1 = 2 * i                              # where the next smaller node hangs
        p0 = 2 * i + 1                          # where the next larger node hangs
        len0 = len1 = 0
        best = 1
        found = []
        left = cut
        while True:
            if cur < 0 or i - cur > MAX_OFFSET or not left:
                son[p0] = son[p1] = -1
                return found
            left -= 1
            ln = len0 if len0 < len1 else len1
            if data[cur + ln] == data[i + ln]:
                ln += 1
                if ln < lim:
                    ln += _match_len(data, cur + ln, i + ln, lim - ln)
                if ln > best:
                    best = ln
                    found.append((i - cur, ln))
                    if ln >= lim:
                        son[p1] = son[2 * cur]
                        son[p0] = son[2 * cur + 1]
                        return found
            if data[cur + ln] < data[i + ln]:
                son[p1] = cur
                p1 = 2 * cur + 1
                cur = son[p1]
                len1 = ln
            else:
                son[p0] = cur
                p0 = 2 * cur
                cur = son[p0]
                len0 = ln

    return insert


def parse(data, cut=256, keep=8, nice=96, force=512, progress=None):
    """-> list of tokens for encode().

    cut: match-tree nodes visited per byte; keep: arrivals kept per
    position; nice: match lengths above this are tried at full length only;
    force: a match this long is taken outright and its bytes skipped.
    """
    n = len(data)
    if n == 0:
        raise ValueError("nothing to pack")
    K = keep
    bt_par = array('i', [-1]) * ((n + 1) * K)       # parent slot index
    bt_tok = array('q', [0]) * ((n + 1) * K)        # type | len << 2 | offset << 20
    pend = {0: {(K_START << 16) | 1: (0, 0, -1, 0)}}   # pos -> key -> (cost, litlen, parent, tok)
    tree = match_tree(data, cut)
    final = None
    skip_to = 0
    t0 = time.time()
    next_report = 65536

    for i in range(n + 1):
        d = pend.pop(i, None)
        found = tree(i) if i < n else ()
        if d is None or i < skip_to:
            continue
        arr = sorted(d.items(), key=lambda kv: kv[1][0])[:K]
        base = i * K
        for s, (key, v) in enumerate(arr):
            bt_par[base + s] = v[2]
            bt_tok[base + s] = v[3]
        if i == n:
            final = base                            # slot 0 is the cheapest
            break
        if progress and i >= next_report:
            next_report += 65536
            progress(i, n, time.time() - t0)

        lim = min(n - i, page_room(i))              # a match stays inside its page
        moff = max_offset(i)

        # literals: one byte from every arrival
        nd = pend.get(i + 1)
        if nd is None:
            nd = pend[i + 1] = {}
        for s, (key, v) in enumerate(arr):
            kind, off = key >> 16, key & 0xFFFF
            cost, ll = v[0], v[1]
            if kind == K_LIT:
                if ll >= MAX_LIT:
                    continue
                c = cost + 8 + EB[ll + 1] - EB[ll]
                nl = ll + 1
            else:
                c = cost + (10 if kind == K_MATCH else 9)
                nl = 1
            old = nd.get(off)                       # (K_LIT << 16) | off == off
            if old is None or c < old[0]:
                nd[off] = (c, nl, base + s, T_LIT)

        longest = 0

        # rep matches: only straight after literals
        for s, (key, v) in enumerate(arr):
            if key >> 16 != K_LIT:
                continue
            r = key & 0xFFFF
            if r > moff or data[i - r] != data[i]:
                continue
            L = _match_len(data, i - r, i, lim)
            cost = v[0]
            mkey = (K_MATCH << 16) | r
            ks = range(1, L + 1) if L <= nice else list(range(1, nice + 1)) + [L]
            for k in ks:
                c = cost + 1 + EB[k]
                pd = pend.get(i + k)
                if pd is None:
                    pd = pend[i + k] = {}
                old = pd.get(mkey)
                if old is None or c < old[0]:
                    pd[mkey] = (c, 0, base + s, T_REP | k << 2)
            if L > longest:
                longest = L

        # new offsets: the tree's matches, cut to the page, then each length
        # from the nearest offset that reaches it
        if found and lim >= 2:
            o, L = found[-1]
            if L >= TREE_LEN and lim > TREE_LEN:    # (the tree stops comparing there)
                found[-1] = (o, _match_len(data, i - o, i, lim))
            bc = arr[0][1][0]
            k = 2
            for o, L in found:
                if L > lim:
                    L = lim
                if L < k:
                    continue
                mc = bc + 8 + EB[(o - 1) // 128 + 1]
                mkey = (K_MATCH << 16) | o
                tk = T_NEW | o << 20
                if L <= nice:
                    ks = range(k, L + 1)
                else:
                    ks = list(range(k, nice + 1)) + [L]
                k = L + 1
                for kk in ks:
                    c = mc + EB[kk - 1]
                    pd = pend.get(i + kk)
                    if pd is None:
                        pd = pend[i + kk] = {}
                    old = pd.get(mkey)
                    if old is None or c < old[0]:
                        pd[mkey] = (c, 0, base, tk | kk << 2)
            if k - 1 > longest:
                longest = k - 1

        if longest >= force:
            skip_to = i + longest                   # take it: nothing inside it is expanded

    if final is None:
        raise ValueError("can't pack: over 64K with no repeat in it")

    # walk back
    toks = []
    s = final
    while s >= 0:
        par = bt_par[s]
        if par < 0:
            break
        tok = bt_tok[s]
        typ, ln, off = tok & 3, (tok >> 2) & 0x3FFFF, tok >> 20
        pos = s // K
        if typ == T_LIT:
            toks.append(('l', pos - 1))
        elif typ == T_REP:
            toks.append(('R', ln))
        else:
            toks.append(('N', off, ln))
        s = par
    toks.reverse()

    out = []                                        # literal bytes -> runs
    for t in toks:
        if t[0] == 'l':
            if out and out[-1][0] == 'L' and out[-1][1] + out[-1][2] == t[1]:
                out[-1] = ('L', out[-1][1], out[-1][2] + 1)
            else:
                out.append(('L', t[1], 1))
        else:
            out.append(t)
    return out


TOOLS = Path(__file__).resolve().parents[2] / "Tools"


def _native_path():
    """where this machine's C packer should be, or None: no build for it"""
    system, machine = platform.system(), platform.machine().lower()
    if system == "Windows":
        return TOOLS / "lzbank.exe"
    if system == "Darwin":
        return TOOLS / "lzbank-macos"
    if system == "Linux":
        if machine in ("x86_64", "amd64"):
            return TOOLS / "lzbank"
        if machine in ("aarch64", "arm64"):
            return TOOLS / "lzbank-linux-arm64"
        if machine.startswith("arm"):
            return TOOLS / "lzbank-linux-arm"
    return None


_warned = False


def native():
    """Tools/lzbank (.exe, -macos, -linux-arm64, -linux-arm) for this machine,
    or None. It is the C port of this file and writes the same bytes, 50x
    faster. LZBANK_PYTHON=1 skips it. If there isn't one, says so once on
    stderr, so a slow pack is never a silent one."""
    global _warned
    if os.environ.get("LZBANK_PYTHON"):
        return None
    exe = _native_path()
    if exe is not None and exe.exists():
        return exe
    if not _warned:
        _warned = True
        what = (f"{exe.relative_to(TOOLS.parent)} not found" if exe is not None
                else f"no C packer for {platform.system()} {platform.machine()}")
        print(f"lzbank: {what}: using the Python packer (about 50x slower). "
              f"Build it with Tools/src/lzbank/build.sh", file=sys.stderr)
    return None


def _pack_native(exe, data, kw):
    """the packed stream from the native packer, or None if kw asks for
    settings it has no option for (it does the defaults and -x)"""
    big = dict(cut=1024, keep=16, nice=256)
    plain = {k: v for k, v in kw.items() if k != "progress"}
    if plain not in ({}, big):
        return None                                 # settings only the Python takes
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.bin", Path(tmp) / "out.lzb"
        src.write_bytes(data)
        args = [str(exe), "-o", str(dst), str(src)]
        if plain == big:
            args.insert(1, "-x")
        if kw.get("progress") is None:
            args.insert(1, "-q")
        r = subprocess.run(args, stdout=subprocess.DEVNULL)
        if r.returncode:
            raise ValueError(f"{exe.name} failed ({r.returncode})")
        return dst.read_bytes()


def pack(data, **kw):
    """data -> the packed stream. Runs the native packer when there is one
    (same bytes), else pack_python()."""
    exe = native()
    if exe is not None:
        packed = _pack_native(exe, bytes(data), kw)
        if packed is not None:
            return packed
    return pack_python(data, **kw)


def pack_python(data, **kw):
    tokens = parse(data, **kw)
    packed = encode(data, tokens)
    back, used = decode(packed, check_rules=True)
    if back != bytes(data) or used != len(packed):
        raise RuntimeError("internal error: the packed stream does not unpack to the input")
    return packed


def bundle(files, **kw):
    """Several files as one: [count][offset of each, from the bundle's start]
    [the packed streams], all words little-endian. Each stream is packed on
    its own (matches never reach into another), and every offset has to fit
    LZDepackBankAt's 16-bit srcoffset, so the last stream must start in the
    first 64K. The table sits in the bundle's first page."""
    head = 2 + 2 * len(files)
    if head > PAGE:
        raise ValueError(f"{len(files)} files: the table would pass the first page")
    streams = [pack(f, **kw) for f in files]
    offsets, at = [], head
    for st in streams:
        if at > 0xFFFF:
            raise ValueError(f"stream {len(offsets)} would start at {at}: past LZDepackBankAt's 64K reach")
        offsets.append(at)
        at += len(st)
    out = bytearray(len(files).to_bytes(2, "little"))
    for o in offsets:
        out += o.to_bytes(2, "little")
    for st in streams:
        out += st
    return bytes(out), offsets, streams


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pack a file into a ZX0 v2 bank stream for LZDepackBank.")
    ap.add_argument("input", nargs="+")
    ap.add_argument("-o", "--output", help="output file (default: input + .lzb, or .unp with -d)")
    ap.add_argument("-b", "--bundle", action="store_true", help="pack each input on its own into one bundle, behind a table of offsets")
    ap.add_argument("-d", "--decompress", action="store_true", help="unpack a .lzb instead")
    ap.add_argument("-x", "--max", action="store_true", help="slower, slightly smaller: a deeper match search, more arrivals")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)

    if a.bundle:
        if not a.output:
            ap.error("a bundle needs -o")
        kw = dict(cut=1024, keep=16, nice=256) if a.max else {}
        files = [Path(f).read_bytes() for f in a.input]
        try:
            out, offsets, streams = bundle(files, **kw)
        except ValueError as e:
            print(f"bundle: {e}", file=sys.stderr)
            return 1
        Path(a.output).write_bytes(out)
        if not a.quiet:
            for k, (f, o, st) in enumerate(zip(a.input, offsets, streams)):
                print(f"  {k}  offset {o:5}  {len(files[k]):6} -> {len(st):5}  {Path(f).name}")
            print(f"{len(files)} files, {sum(map(len, files))} -> {len(out)} bytes, "
                  f"{(len(out) + PAGE - 1) // PAGE} pages -> {a.output}")
        return 0
    if len(a.input) > 1:
        ap.error("several inputs make a bundle: add -b")

    src = Path(a.input[0])
    data = src.read_bytes()
    if a.decompress:
        try:
            out, _ = decode(data, check_rules=True)
        except (ValueError, IndexError) as e:
            print(f"{src.name}: not an LZBank stream ({e})", file=sys.stderr)
            return 1
        dst = Path(a.output) if a.output else src.with_suffix(".unp")
        dst.write_bytes(out)
        if not a.quiet:
            print(f"{src.name}: {len(data)} -> {len(out)} bytes, {dst}")
        return 0

    kw = dict(cut=1024, keep=16, nice=256) if a.max else {}
    if not a.quiet:
        kw["progress"] = lambda i, n, t: print(f"\r  {i * 100 // n}%  {t:.0f}s", end="", file=sys.stderr, flush=True)
    t0 = time.time()
    packed = pack(data, **kw)
    if not a.quiet:
        print("\r" + " " * 20 + "\r", end="", file=sys.stderr)
    dst = Path(a.output) if a.output else src.with_name(src.name + ".lzb")
    dst.write_bytes(packed)
    if not a.quiet:
        pin = (len(data) + PAGE - 1) // PAGE
        pout = (len(packed) + PAGE - 1) // PAGE
        print(f"{src.name}: {len(data)} -> {len(packed)} bytes ({len(packed) * 100 / len(data):.1f}%), "
              f"{pout} page{'s' if pout != 1 else ''} unpack to {pin}, {time.time() - t0:.1f}s -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
