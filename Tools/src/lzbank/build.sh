#!/bin/sh
# Build lzbank into Tools/.
#
#   Linux:  sh Tools/src/lzbank/build.sh        -> Tools/lzbank              (static, x86-64)
#                                                  Tools/lzbank-linux-arm64  (on 64-bit ARM)
#                                                  Tools/lzbank-linux-arm    (on 32-bit ARM)
#   macOS:  sh Tools/src/lzbank/build.sh        -> Tools/lzbank-macos  (universal: arm64 + x86-64)
#   Either, with a cross compiler on PATH, also:
#     x86_64-w64-mingw32-gcc  -> Tools/lzbank.exe
#     zig                     -> all of them: Tools/lzbank.exe, Tools/lzbank-macos (arm64 +
#                                x86-64, joined by lipo.py: zig has no lipo of its own), and
#                                the Linux ones for the other architectures (static, musl)
#
# Windows itself: build.bat. After building, check it against the Python packer:
#   python/bin/python Sources/LZBank/tests/test_lzbank.py
set -e
cd "$(dirname "$0")"
OUT=../..
FLAGS="-O2 -std=c99 -Wall"

case "$(uname -s)" in
    Darwin)
        cc $FLAGS -arch arm64 -arch x86_64 -mmacosx-version-min=10.13 -o $OUT/lzbank-macos lzbank.c
        echo "built $OUT/lzbank-macos"
        ;;
    *)
        case "$(uname -m)" in
            x86_64|amd64)   NAME=lzbank ;;
            aarch64|arm64)  NAME=lzbank-linux-arm64 ;;
            arm*)           NAME=lzbank-linux-arm ;;
            *)              NAME=lzbank-linux-$(uname -m) ;;
        esac
        ${CC:-cc} $FLAGS -static -s -o $OUT/$NAME lzbank.c
        echo "built $OUT/$NAME"
        ;;
esac

if command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
    x86_64-w64-mingw32-gcc $FLAGS -s -static -o $OUT/lzbank.exe lzbank.c
    echo "built $OUT/lzbank.exe"
elif command -v zig >/dev/null 2>&1; then
    zig cc $FLAGS -target x86_64-windows-gnu -s -o $OUT/lzbank.exe lzbank.c
    echo "built $OUT/lzbank.exe"
fi
if [ "$(uname -s)" != Darwin ] && command -v zig >/dev/null 2>&1; then
    zig cc $FLAGS -target aarch64-macos.11.0 -o lzbank-arm64.tmp lzbank.c
    zig cc $FLAGS -target x86_64-macos.10.13 -o lzbank-x64.tmp lzbank.c
    ${PYTHON:-python3} lipo.py -o $OUT/lzbank-macos lzbank-arm64.tmp lzbank-x64.tmp
    rm -f lzbank-arm64.tmp lzbank-x64.tmp
    echo "built $OUT/lzbank-macos (arm64 + x86-64)"
fi
if command -v zig >/dev/null 2>&1; then
    for t in x86_64-linux-musl:lzbank aarch64-linux-musl:lzbank-linux-arm64 \
             arm-linux-musleabihf:lzbank-linux-arm; do
        name=${t#*:}
        [ -f "$OUT/$name" ] && [ "$name" = "${NAME:-}" ] && continue    # the native build
        cpu=""
        [ "$name" = lzbank-linux-arm ] && cpu=-mcpu=arm1176jzf_s   # ARMv6: Pi Zero/1 up
        zig cc $FLAGS -target ${t%%:*} $cpu -static -s -o $OUT/$name lzbank.c
        echo "built $OUT/$name"
    done
fi
rm -f $OUT/*.pdb
