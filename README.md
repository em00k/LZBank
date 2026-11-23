# LZBank

Pack big data files on your computer, and unpack them on the Next across as
many 8K banks as they need, with one call.

Six 320x256 pictures (576K) pack into 9 banks and unpack in about one
second. The packing is as tight as ZX0.

## Installing

LZBank is part of NextBuild. If you have this repository on its own, copy
its `Scripts` and `Tools` folders over the ones in your NextBuild folder:

| Copied to NextBuild | What it is |
|---|---|
| `Scripts/nextlib_lzbank.bas` | the include for your programs |
| `Tools/lzbank`, `lzbank.exe`, `lzbank-macos`, `lzbank-linux-arm64`, `lzbank-linux-arm` | the packer, one program per system |
| `Scripts/tools/lzbank.py` | the same packer in Python |
| `Tools/src/lzbank/` | the packer's C source and build scripts |

Put the rest of the repository (the examples) in `Sources/LZBank/`.

## Quick start

**1. Pack the file** on your computer with the `lzbank` packer in
NextBuild's `Tools/` folder, here a 320x256 picture:

| System | Command |
|---|---|
| Windows | `Tools\lzbank.exe data\title.nxi` |
| macOS | `Tools/lzbank-macos data/title.nxi` |
| Linux on a PC (x86-64) | `Tools/lzbank data/title.nxi` |
| Linux on ARM (64-bit) | `Tools/lzbank-linux-arm64 data/title.nxi` |
| Linux on ARM (32-bit) | `Tools/lzbank-linux-arm data/title.nxi` |

The table in [The packer](#the-packer) says which ARM version a Raspberry Pi needs.

This writes `data/title.nxi.lzb` and tells you how many banks the packed
file takes ("pages") and how many it unpacks to:

```
title.nxi: 82432 -> 6028 bytes (7.3%), 1 page unpack to 11, 0.1s -> data/title.nxi.lzb
```

**2. Load it** into the NEX:

```basic
LoadSDBank("title.nxi.lzb", 0, 0, 0, 48)        ' the packed file, in bank 48
```

**3. Unpack it** when you need it:

```basic
#include <nextlib_lzbank.bas>

LZDepackBank(48, 64)                            ' from bank 48 into banks 64-74
```

That's all. The file comes out exactly as it went in, from the start of
bank 64.

## The examples

| File | What it does |
|---|---|
| `LZBank.bas` | Unpacks six pictures (576K) from one packed file with a single `LZDepackBank`, then shows them one at a time. Press any key for the next |
| `LZBankAt.bas` | The same pictures packed as a *bundle* (see below). A slideshow that unpacks each picture when it's needed, off screen, then swaps it in |

Build either one like any other NextBuild program, for example:

```
python Scripts/nextbuild.py -b Sources/LZBank/LZBank.bas
```

## Several files in one: bundles

A bundle keeps several files in one packed file so you can unpack any one
of them on its own: a picture per level, a set of title screens.

```
Tools/lzbank -b title.nxi level1.nxi level2.nxi -o data/screens.lzb
```

The bundle starts with a small table: the number of files, then where each
one starts. Read it with `BankPeekUint` and unpack with `LZDepackBankAt`:

```basic
LoadSDBank("screens.lzb", 0, 0, 0, 48)

count  = BankPeekUint(48, 0)                              ' how many files
offset = BankPeekUint(48, 2 + cast(uinteger, n) * 2)      ' where file n starts
LZDepackBankAt(48, offset, 64)                            ' unpack file n into bank 64
```

`LZDepackBankAt(bank, offset, destination)` works for any packed data that
doesn't start at the beginning of a bank, not only bundles.

A bundle can hold up to 64K of packed data. One big file packs a little
smaller than a bundle of the same data, so use a bundle when you need the
parts one at a time.

## Good to know

- **Pack with `lzbank`.** Files packed with the `zx0` tool, or with
  `gfx2next -zx0`, won't unpack correctly. Pack the original file again with
  `lzbank`.
- **Plan your banks.** Give the packed file the banks the packer tells you,
  and make sure the banks you unpack into don't overlap them.
- **Unpacking always starts at the beginning of the destination bank.**
- **Music keeps playing.** Interrupts stay on while it unpacks, and NextBuild's
  music and sound effect players are safe to run alongside it.
- **It can go in a CODEBANK**, so it costs no room in your main program:
  ```basic
  #pragma codebank = 1
  #include <nextlib_lzbank.bas>
  #pragma codebank = 0
  ```
- **Speed:** about one second for 576K, a sixth of a second for one 80K
  picture, at 28MHz. Unpack between scenes or behind a hidden screen.
- To show a Layer 2 picture without it appearing in front of you, unpack it
  into banks that aren't on screen, then point NextReg `$12` at them.
  `LZBankAt.bas` shows how.

## The packer

Use the `lzbank` program for your system, in NextBuild's `Tools/` folder:

| System | Program |
|---|---|
| Windows (x64) | `Tools\lzbank.exe` |
| macOS (Apple Silicon and Intel) | `Tools/lzbank-macos` |
| Linux on a PC (x86-64) | `Tools/lzbank` |
| Linux on ARM, 64-bit: a Raspberry Pi 3, 4 or 5 with a 64-bit OS | `Tools/lzbank-linux-arm64` |
| Linux on ARM, 32-bit: any Raspberry Pi with a 32-bit OS | `Tools/lzbank-linux-arm` |

Not sure which Linux you have? `uname -m` prints `x86_64`, `aarch64`
(64-bit ARM) or `armv6l` / `armv7l` (32-bit ARM).

```
lzbank file                   pack (writes file.lzb)
lzbank file -o out.lzb        pack, choosing the name
lzbank -b a b c -o x.lzb      make a bundle
lzbank -d x.lzb -o x.bin      unpack, to check a file
    -x   pack harder: slower, a little smaller
    -q   quiet
```

It packs 576K in about half a second.

On a Mac, if the system says the program can't be opened because it's from
an unidentified developer, allow it in System Settings → Privacy & Security,
or run `xattr -d com.apple.quarantine Tools/lzbank-macos` once.

`Scripts/tools/lzbank.py` is the same packer written in Python, with the same
options and exactly the same output. It's there for your own Python scripts
and for systems with no `lzbank` program. It runs the right `lzbank` program
itself when there is one, so it's just as fast. When there isn't one it packs
in Python, about 50 times slower, and prints a message saying so.

## Rebuilding the example data

The examples use pictures from `Sources/ReadIFF/assets/`. To pack them again
(these scripts use the `lzbank` program):

```
python Sources/LZBank/tools/make_pictures.py      (data/pictures.lzb, for LZBank.bas)
python Sources/LZBank/tools/make_slides.py        (data/slides.lzb, for LZBankAt.bas)
```
