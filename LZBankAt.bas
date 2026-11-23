' Project: LZBank - LZDepackBankAt example
' A slideshow from a bundle: six 320x256 pictures packed one after another
' into one file, data/slides.lzb, behind a table of where each starts.
' LZDepackBankAt unpacks any one of them by its offset into the bundle.
' Each picture goes into the Layer 2 buffer that isn't on screen, and the
' two swap at the bottom of the frame: no blank, no half-drawn picture.
' Any key shows the next one.
'
' The bundle (Scripts/tools/lzbank.py -b, all words little-endian):
'   +0       number of pictures
'   +2       offset of picture 0's stream, from the start of the bundle
'   +4       offset of picture 1's stream ...
'   then the streams. An offset can be past the first page (picture 5's
'   is 41806: page BK_SLIDES + 5, byte 852); LZDepackBankAt finds it.
'
' Display: Layer 2 320x256, double buffered with NextReg $12.
'
' Memory map (8K pages):
'   18-28    buffer A: pixels 18-27, palette at the start of 28 (NR $12 = 9)
'   40       CODEBANK 1: the depacker                   '!codebank=40
'   48-54    data/slides.lzb                            BK_SLIDES
'   64-74    buffer B: pixels 64-73, palette in 74      (NR $12 = 32)
'
' Rebuild the data: python/bin/python Sources/LZBank/utils/make_slides.py
' Tests:            python/bin/python Sources/LZBank/tests/test_lzbank.py

'!org=$8000
'!codebank=40
'!opt=4

#define NEX
#define IM2

#include <nextlib.bas>

CONST BK_SLIDES as ubyte = 48
CONST BUF_A     as ubyte = 18
CONST BUF_B     as ubyte = 64

LoadSDBank("slides.lzb",0,0,0,BK_SLIDES)                    ' the bundle

#pragma codebank = 1
#include <nextlib_lzbank.bas>
#pragma codebank = 0

dim count  as ubyte                                         ' pictures in the bundle
dim slide  as ubyte = 0
dim hidden as ubyte = BUF_B                                 ' the buffer not on screen
dim offset as uinteger

InitLayer2(MODE320X256)                                     ' clears buffer A
ShowLayer2(1)

count = BankPeekUint(BK_SLIDES, 0)

do
    offset = BankPeekUint(BK_SLIDES, 2 + cast(uinteger, slide) * 2)
    LZDepackBankAt(BK_SLIDES, offset, hidden)               ' unpack it off screen

    WaitRaster(224)                                         ' below the picture's last line
    NextRegA($12, hidden >> 1)                              ' show it (16K bank number)
    InitPalette(L2_PALETTE_P1, hidden + 10, 0, 0, 0)        ' and its palette

    if hidden = BUF_B then
        hidden = BUF_A
    else
        hidden = BUF_B
    end if
    slide = slide + 1
    if slide = count then slide = 0

    WaitKey()
loop
