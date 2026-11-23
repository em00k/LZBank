' ---------------------------------------------------------------
' nextlib_lzbank.bas - unpack a long stream across 8K banks
' David Saphier / em00k
'
'   LZDepackBank(srcbank, dstbank)
'       unpack the stream at the start of page srcbank into page dstbank
'       and on, however many pages it takes on both sides
'   LZDepackBankAt(srcbank, srcoffset, dstbank)
'       the same, the stream starting srcoffset bytes in (it may be past
'       the first page: 9000 is page srcbank+1, byte 808). For a bundle,
'       several streams packed back to back by lzbank -b:
'           n   = BankPeekUint(srcbank, 0)                   ' how many
'           off = BankPeekUint(srcbank, 2 + cast(uinteger, k) * 2)
'           LZDepackBankAt(srcbank, off, dstbank)            ' number k
'
' Pack with the C packer in Tools/: lzbank (Linux x86-64), lzbank.exe,
' lzbank-macos, lzbank-linux-arm64, lzbank-linux-arm:
'   Tools/lzbank data/level.bin                         -> data/level.bin.lzb
'   Tools/lzbank -b a.nxi b.nxi -o data/set.lzb         a bundle
' (Scripts/tools/lzbank.py is the same packer in Python, same output.)
'
' The stream is ZX0 v2 (Einar Saukas' format) with one rule added by the
' packer: no match crosses an 8K page of the output. A plain zx0 file
' breaks the rule and unpacks wrong. The output starts at byte 0 of
' dstbank, and is as long as the file that was packed.
'
' Paging: the packed data comes in through slot 2 ($4000), the output
' goes out through slot 1 ($2000) with the page before it in slot 0, so a
' match from up to 8K back is one LDIR; one from further back (ZX0 reaches
' 32640) pages its own start through slot 0. Slots 0-2 are put back as
' they were. Slot 3 is never touched, so the routine can live in a
' CODEBANK, and the stack must not be in $0000-$5FFF (nextlib's isn't).
' Interrupts stay on: the music/sfx ISRs save and restore the slots they
' page. An ISR of your own must do the same.
'
' Speed, measured in the simulator: about 47 T a byte on pictures, 24 on
' data that doesn't pack; 576K in about 50 frames at 28MHz. 459 bytes.
' Examples: Sources/LZBank/LZBank.bas (576K in one call), LZBankAt.bas
' (a bundle, double-buffered slideshow); tests in tests/test_lzbank.py.
'
' Needs nextlib.bas included first.
' ---------------------------------------------------------------

#ifndef __NEXTLIB_LZBANK__
#define __NEXTLIB_LZBANK__

#ifndef __NEXTLIB__
#error "nextlib_lzbank.bas needs #include <nextlib.bas> before it"
#endif

#pragma push(case_insensitive)
#pragma case_insensitive = TRUE
#pragma zxnext = TRUE

sub LZDepackBank(byval srcbank as ubyte, byval dstbank as ubyte)
    LZDepackBankAt(srcbank, 0, dstbank)
end sub

sub LZDepackBankAt(byval srcbank as ubyte, byval srcoffset as uinteger, byval dstbank as ubyte)
    asm
        ld      b,(ix+5)                    ; srcbank
        ld      l,(ix+6)
        ld      h,(ix+7)                    ; srcoffset
        ld      c,(ix+9)                    ; dstbank
        call    lzb_depack
        jp      lzb_end

; ---------------------------------------------------------------------------
;  lzb_depack: B = packed page, HL = offset into it (any 16 bits), C = the
;  first output page. Uses AF BC DE HL; IX, IY and the alternate set kept.
;  Einar Saukas & Urusergi's dzx0_standard, with its pointers made to run
;  over pages:
;    HL  packed data in slot 2 ($4000-$5FFF), next page at $6000
;    DE  output in slot 1 ($2000-$3FFF), the page before it in slot 0;
;        next page when DE reaches $4000, which a match never crosses
;    A   the bit buffer, BC counts, the last offset (negative) on the stack
; ---------------------------------------------------------------------------
lzb_depack:
        push    bc
        ld      bc,$243B                    ; save MMU0-2 into the exit nextregs
        ld      a,$50
        out     (c),a
        inc     b
        in      a,(c)
        ld      (lzb_r0+3),a
        dec     b
        ld      a,$51
        out     (c),a
        inc     b
        in      a,(c)
        ld      (lzb_r1+3),a
        dec     b
        ld      a,$52
        out     (c),a
        inc     b
        in      a,(c)
        ld      (lzb_r2+3),a
        pop     bc

        ld      a,h                         ; the packed data: page B + offset / 8K
        rlca
        rlca
        rlca
        and     7
        add     a,b
        ld      (lzb_sp),a
        nextreg $52,a
        ld      a,h
        and     $1F
        or      $40
        ld      h,a

        ld      a,c                         ; the output: page C in slot 1,
        ld      (lzb_dp),a                  ; the one before it in slot 0
        nextreg $51,a
        dec     a
        nextreg $50,a
        ld      de,$2000

        ld      bc,$FFFF                    ; the last offset: 1
        push    bc
        inc     bc
        ld      a,$80
        jr      lzb_literals

lzb_lit_pages:                              ; literals near a page end: in runs,
        ld      (lzb_cnt),bc                ; each up to the next end on either side
lzb_lp_loop:
        xor     a
        sub     l
        ld      c,a
        ld      a,$60
        sbc     a,h
        ld      b,a                         ; room in the packed page
        push    hl
        ld      hl,(lzb_cnt)
        or      a
        sbc     hl,bc
        jr      nc,lzb_lp_1
        add     hl,bc
        ld      b,h
        ld      c,l                         ; or the count, if less
lzb_lp_1:
        xor     a
        sub     e
        ld      l,a
        ld      a,$40
        sbc     a,d
        ld      h,a                         ; room in the output page
        or      a
        sbc     hl,bc
        jr      nc,lzb_lp_2
        add     hl,bc
        ld      b,h
        ld      c,l                         ; or that, if less
lzb_lp_2:
        ld      hl,(lzb_cnt)
        or      a
        sbc     hl,bc
        ld      (lzb_cnt),hl
        pop     hl
        ldir
        bit     5,h
        call    nz,lzb_snext
        bit     6,d
        call    nz,lzb_dnext
        ld      bc,(lzb_cnt)
        ld      a,b
        or      c
        jr      nz,lzb_lp_loop
        jp      lzb_lit_done                ; (BC = 0, as the decoder needs)

lzb_literals:
        call    lzb_elias                   ; BC literals
        push    af
        ld      a,b                         ; fewer than 256, and neither pointer
        or      a                           ; passes a 256-byte boundary: no
        jr      nz,lzb_lit_pages            ; page end in reach, one LDIR
        ld      a,l
        add     a,c
        jr      c,lzb_lit_pages
        ld      a,e
        add     a,c
        jr      c,lzb_lit_pages
        ldir
lzb_lit_done:
        pop     af
        add     a,a                         ; last offset or a new one?
        jr      c,lzb_new_offset
        call    lzb_elias
lzb_copy:                                   ; BC bytes from the offset back
        ex      (sp),hl
        push    hl
        add     hl,de
        jr      nc,lzb_far                  ; it starts before slot 0
        ldir
lzb_copied:
        pop     hl
        ex      (sp),hl
        bit     6,d                         ; DE at $4000: the next output page
        call    nz,lzb_dnext
        add     a,a                         ; literals or a new offset?
        jr      nc,lzb_literals
lzb_new_offset:
        pop     bc
        ld      c,$FE
        call    lzb_elias_loop              ; the offset's high part
        inc     c
        jp      z,lzb_done                  ; the end marker
        ld      b,c
        ld      c,(hl)                      ; its low part
        inc     hl
        bit     5,h                         ; (bit keeps the carry)
        call    nz,lzb_snext
        rr      b
        rr      c
        push    bc
        ld      bc,1
        call    nc,lzb_elias_back
        inc     bc
        jr      lzb_copy

lzb_far:                                    ; a match from more than a page back:
        push    af                          ; its start through slot 0, a page at a time
        ld      a,h
        sra     a
        sra     a
        sra     a
        sra     a
        sra     a                           ; -4..-1: pages before slot 0's
        push    hl
        ld      hl,lzb_dp
        add     a,(hl)
        dec     a
        pop     hl
        ld      (lzb_xp),a
        nextreg $50,a
        ld      a,h
        and     $1F
        ld      h,a
        ld      (lzb_cnt),bc
lzb_far_loop:
        xor     a
        sub     l
        ld      c,a
        ld      a,$20
        sbc     a,h
        ld      b,a                         ; room in this page
        push    hl
        ld      hl,(lzb_cnt)
        or      a
        sbc     hl,bc
        jr      nc,lzb_far_1
        add     hl,bc
        ld      b,h
        ld      c,l                         ; or the count, if less
        ld      hl,0
lzb_far_1:
        ld      (lzb_cnt),hl
        pop     hl
        ldir
        ld      bc,(lzb_cnt)
        ld      a,b
        or      c
        jr      z,lzb_far_done
        ld      h,0                         ; HL = $0000: it reached $2000
        ld      a,(lzb_xp)
        inc     a
        ld      (lzb_xp),a
        nextreg $50,a
        jr      lzb_far_loop
lzb_far_done:
        ld      a,(lzb_dp)                  ; slot 0 back to the page before the output
        dec     a
        nextreg $50,a
        pop     af
        jp      lzb_copied

lzb_elias:                                  ; interlaced Elias gamma -> BC
        inc     c
lzb_elias_loop:
        add     a,a
        jr      nz,lzb_elias_skip
        ld      a,(hl)
        inc     hl
        bit     5,h
        call    nz,lzb_snext
        rla
lzb_elias_skip:
        ret     c
lzb_elias_back:
        add     a,a
        rl      c
        rl      b
        jr      lzb_elias_loop

lzb_snext:                                  ; HL reached $6000: the next packed page
        push    af
        ld      h,$40
        ld      a,(lzb_sp)
        inc     a
        ld      (lzb_sp),a
        nextreg $52,a
        pop     af
        ret

lzb_dnext:                                  ; DE reached $4000: the next output page
        push    af
        ld      d,$20
        ld      a,(lzb_dp)
        nextreg $50,a
        inc     a
        ld      (lzb_dp),a
        nextreg $51,a
        pop     af
        ret

lzb_done:
lzb_r0:
        nextreg $50,0
lzb_r1:
        nextreg $51,0
lzb_r2:
        nextreg $52,0
        ret

lzb_sp:
        db      0                           ; packed page in slot 2
lzb_dp:
        db      0                           ; output page in slot 1
lzb_xp:
        db      0                           ; a far match's page in slot 0
lzb_cnt:
        dw      0
lzb_end:
    end asm
end sub

#pragma pop(case_insensitive)

#endif
