' Project: LZBank
' One packed stream unpacked across banks: six 320x256 pictures, 576K,
' packed to a few pages with Scripts/tools/lzbank.py and unpacked by one
' LZDepackBank call. Any key shows the next one.
'
' Display: Layer 2 320x256. Each picture is shown where it was unpacked,
' by pointing NextReg $12 at it, with its palette from the page after it.
'
' Memory map (8K pages):
'   18-27    Layer 2 at start (InitLayer2 clears it; the text goes here)
'   32       font5                                      FONT_BANK
'   40       CODEBANK 1: the depacker                   '!codebank=40
'   48-      data/pictures.lzb                          BK_PACKED
'   64-135   six pictures, 12 pages each: pixels in     BK_PICS
'            the first 10, the palette at the start of the 11th
'
' Rebuild the data: python/bin/python Sources/LZBank/utils/make_pictures.py
' Tests:            python/bin/python Sources/LZBank/tests/test_lzbank.py

'!org=$8000
'!codebank=40
'!opt=4

#define NEX
#define IM2

#include <nextlib.bas>

CONST FONT_BANK as ubyte = 32
CONST BK_PACKED as ubyte = 48
CONST BK_PICS   as ubyte = 64
CONST PIC_PAGES as ubyte = 12
CONST NUM_PICS  as ubyte = 6

LoadSDBank("[]font5.fnt",0,0,0,FONT_BANK)                   ' font5 from the system fonts
LoadSDBank("pictures.lzb",0,0,0,BK_PACKED)                  ' the packed stream

#pragma codebank = 1
#include <nextlib_lzbank.bas>
#pragma codebank = 0

dim pic  as ubyte = 0
dim page as ubyte = BK_PICS

InitLayer2(MODE320X256)
ShowLayer2(1)
FL2Text(0,0,"UNPACKING 576K",FONT_BANK)

LZDepackBank(BK_PACKED, BK_PICS)

do
    WaitRaster(224)                                         ' below the picture's last line
    NextRegA($12, page >> 1)                                ' 16K bank of the picture
    InitPalette(L2_PALETTE_P1, page + 10, 0, 0, 0)          ' its palette, 256 colours
    WaitKey()
    pic = pic + 1
    page = page + PIC_PAGES
    if pic = NUM_PICS then
        pic = 0
        page = BK_PICS
    end if
loop
