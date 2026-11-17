/*
 * libtag_c.so.2.0.1: send .m4b to TagLib's existing MP4 cover extractor (docs/internals.md, M4B).
 * tools/build.py (patch_m4b_cover) links this at BASE, the library's new segment, and points the
 * cover dispatcher's M4A branch (0x125ac) here: v0 strcasecmp(ext, "m4a"), s0 the extension.
 * Branches are PC-relative and "m4b" is GP-relative, so it runs wherever the loader puts the
 * library. The addresses are stock's, which TAG_C_SHA pins.
 */
.set noreorder
.set noat
.text

#define GP 0x327e0 /* the library's gp */
/* Stock's code as labels, for the branches: clang reads a branch's number as an offset. */
.set mp4, m4b_cover + 0x126d8 - BASE  /* the dispatcher's MP4 extractor case */
.set next, m4b_cover + 0x125b4 - BASE /* the next format's comparison */

.globl m4b_cover
m4b_cover:
    beqz  $2, mp4             /* M4A: as stock */
    nop                       /* delay slot */
    lw    $25, -0x7e90($28)   /* strcasecmp's GOT slot */
    move  $4, $16             /* the extension */
    addiu $5, $28, %lo(m4b - GP) /* "m4b"; the linker fills it in, as clang 18 takes no later label here */
    jalr  $25                 /* strcasecmp(ext, "m4b") */
    nop                       /* delay slot */
    beqz  $2, mp4             /* M4B: the MP4 extractor */
    lw    $28, 0x10($sp)      /* gp back, after the call */
    b     next                /* anything else: the next format */
    nop                       /* delay slot */
m4b:
    .asciz "m4b"
