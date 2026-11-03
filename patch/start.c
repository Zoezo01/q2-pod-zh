/* The helpers' entry point (video.c, boot.c). glibc's MIPS __start: main, argc, argv, no init or
 * fini, the loader's rtld_fini, stack_end. */
__asm__(".set noreorder\n.globl __start\n__start:\n"
        "lui $28, %hi(_gp)\naddiu $28, $28, %lo(_gp)\n"
        "move $31, $0\nlw $5, 0($29)\naddiu $6, $29, 4\n"
        "li $8, -8\nand $29, $29, $8\naddiu $29, $29, -32\n"
        "lui $4, %hi(main)\naddiu $4, $4, %lo(main)\nmove $7, $0\n"
        "sw $0, 16($29)\nsw $2, 20($29)\nsw $29, 24($29)\n"
        "jal __libc_start_main\nnop\n1: b 1b\nnop\n.set reorder\n");

/* crt1.o's marker: without it glibc takes the program for a libc5-era one and gives it old-layout
 * stdio FILEs, which crash exit() */
const int _IO_stdin_used = 0x20001;
