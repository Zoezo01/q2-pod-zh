SECTIONS {
  . = 0xb00000;
  .text : { *(.text*) }
  .rodata : { *(.rodata*) }
  /* Zero-filled writable state; the builder maps it R/W past the payload text. */
  .scratch 0xb80000 (NOLOAD) : { __scratch_start = .; *(.scratch) *(.bss*) __scratch_end = .; }
  /DISCARD/ : { *(.comment) *(.note*) *(.pdr) *(.mdebug*) *(.MIPS.abiflags) *(.reginfo) }
}
