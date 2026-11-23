#!/usr/bin/env python3
"""Join thin Mach-O executables into one universal (fat) binary, like
Apple's `lipo -create`, for building the macOS lzbank off a Mac with zig.

    python lipo.py -o lzbank-macos lzbank-macos-arm64 lzbank-macos-x64

Each slice keeps its own code signature (zig ad-hoc signs arm64, which
Apple Silicon needs to run anything); the fat header around them is not
signed, as with lipo's own output.
"""
import struct
import sys
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
LC_CODE_SIGNATURE = 0x1D
ALIGN = {0x0100000C: 14, 0x01000007: 12}         # arm64: 16K pages, x86-64: 4K


def thin(data, name):
    magic, cputype, cpusub, _, ncmds, _, _, _ = struct.unpack_from("<8I", data)
    if magic != MH_MAGIC_64:
        sys.exit(f"{name}: not a 64-bit Mach-O")
    off, signed = 32, False
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<2I", data, off)
        signed |= cmd == LC_CODE_SIGNATURE
        off += size
    return cputype, cpusub, signed


def main(argv):
    if len(argv) < 4 or argv[0] != "-o":
        sys.exit("usage: lipo.py -o out thin1 thin2 ...")
    out, files = Path(argv[1]), [Path(f) for f in argv[2:]]
    slices = []
    for f in files:
        data = f.read_bytes()
        cpu, sub, signed = thin(data, f.name)
        if cpu == 0x0100000C and not signed:
            sys.exit(f"{f.name}: arm64 without a code signature would not run")
        slices.append((cpu, sub, data))
    head = 8 + 20 * len(slices)
    body, entries, at = bytearray(), [], head
    for cpu, sub, data in slices:
        align = ALIGN.get(cpu, 14)
        at = (at + (1 << align) - 1) & -(1 << align)
        entries.append(struct.pack(">5I", cpu, sub, at, len(data), align))
        body += bytes(at - head - len(body)) + data
        at += len(data)
    out.write_bytes(struct.pack(">2I", FAT_MAGIC, len(slices)) + b"".join(entries) + body)
    out.chmod(0o755)


if __name__ == "__main__":
    main(sys.argv[1:])
