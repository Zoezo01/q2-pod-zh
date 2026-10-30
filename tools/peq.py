"""Checked Q2 V1.32 PEQ hooks and target ABI imports; no vendor code is distributed."""
import re
import struct
from build import ROOT, FLAGS, GP, FUNCTIONS, GLOBALS, PRIVATE_FUNCTIONS, append_payload, check, fileoff, run, sha, symbols, jal
from ipod import patch_word

PLAYER_SHA = '9c3f8c6d01f1ba62392622f6098b06a36b3e4f022a5468eaca6a5803e74f8e11'
PLAYER_BASE = 0xe10000  # stock final LOAD ends at 0xe03b58
# demux_audio_open's branch to the whole-file frame walk for Xing/VBRI MP3s; see docs/internals.md.
VBR_SCAN = 0x48fb3c
# MPlayer's pause loop sleeps usec_sleep(20000) between command checks: 50 wakes a second for as long as the
# track stays paused. Its sleep becomes a futex wait that the command thread's handler call wakes
# (hci_pause_wait, hci_command in peq_player.c, docs/internals.md#battery).
PAUSE_WAIT = (0x430230, 0x0c12931c)  # jal usec_sleep
CMD_DONE = (0x42d7c4, 0x0c10af88)  # jal 0x42be20, the command handler
# Stopped, the idle loop's usleep(10000) becomes a futex wait on the play flag, which the command handler's
# wrapper wakes (CMD_DONE; peq_player.c, docs/internals.md#battery).
IDLE_WAIT = (0x42d9c8, 0x0c2b6bf4)  # jal usleep
# The audio demuxer's seek slot (demuxer_desc_audio at 0x89fd90) holds demux_audio_seek; see docs/internals.md.
SEEK_SLOT, STOCK_SEEK = 0x89fdbc, 0x48d1ac
# demux_audio_open's calls to the ID3v2 frame walker (0x48e6b0) that feeds ReplayGain; see docs/internals.md.
ID3_CALLS, STOCK_ID3 = (0x48f704, 0x4923d8), 0x48e6b0
# Audio private allocation, pre-filter PCM call, and audio demuxer close slot.
MP3_ALLOC, MP3_FILTER, MP3_CLOSE = 0x48fb90, 0x47f8e0, 0x89fdb8
# ad_libmad read_frame's demux_read_data call (0x484c0c), which gets the last frame its guard bytes.
MP3_READ, DEMUX_READ = 0x4b248c, 0x484c0c
# The equalizer filter's descriptor (pinned) and its open slot, which becomes peq_open.
EQ_DESC, EQ_OPEN_SLOT = 0x893df8, 0x893e0c
# lavcresample's descriptor open slot (af_info at 0x8a4540; stock open 0x4ac2c0), which becomes rs_open: the
# resampler Bluetooth's 48 kHz output appends keeps its state across a gapless change (docs/internals.md).
RS_OPEN_SLOT, RS_OPEN = 0x8a4554, 0x4ac2c0
# Stock skips the equalizer above 48 kHz (address, stock word, patched word); see docs/internals.md.
EQ_RATE_GATES = ((0x42ced0, 0x10400098, 0),            # beqz $v0 to "setequalizer off" -> nop
                 (0x42fd84, 0x1440003b, 0x1000003b))   # bnez $v0 to the insert -> b
# The MQA side chain (docs/internals.md#mqa-detection): fill_audio_out_buffers runs every probed stream
# through the MQA decoder while isMQA (0xb10c54) > 0 but plays its output only when isMQA == 2, so the gate
# becomes isMQA == 2 (address, stock word, patched word).
MQA_GATE = ((0x42f8b0, 0x34620040, 0x3c0200b1),   # ori $v0, $v1, 0x40    -> lui $v0, 0xb1
            (0x42f8b4, 0x005d1021, 0x8c420c54),   # addu $v0, $v0, $sp    -> lw $v0, 0xc54($v0)
            (0x42f8b8, 0x8c420000, 0x2442fffe),   # lw $v0, 0($v0)        -> addiu $v0, $v0, -2
            (0x42f8bc, 0x8c420c54, 0),            # lw $v0, 0xc54($v0)    -> nop
            (0x42f8c0, 0x1840000a, 0x1440000a))   # blez $v0, 0x42f8ec    -> bnez $v0, 0x42f8ec
# So MQA is found by the start-of-track probe alone. The detector gives up after 96000 frames, so the probe
# reads 0x300000 >> (codec ^ 15) bytes instead of 0xc00 (0x20000 on output way 2): FLAC (13, decoded to 8-byte
# frames at 24 bit) 0xc0000, past 96000 frames for every eligible format; PCM (11) 0x30000, ad_pcm's a_buffer
# (0x30800) holding no more. $t3 is the codec type here and $v1 is dead.
MQA_PROBE = ((0x42eaf4, 0x3c020002, 0x3c020030),  # lui $v0, 0x2           -> lui $v0, 0x30
             (0x42eaf8, 0x38630002, 0x3963000f),  # xori $v1, $v1, 2       -> xori $v1, $t3, 15
             (0x42eafc, 0x00e3100b, 0x00621006))  # movn $v0, $a3, $v1     -> srlv $v0, $v0, $v1
LIBC = {
    'memset': ('void *', 'void *, int, unsigned'),
    'memcpy': ('void *', 'void *, const void *, unsigned'),
    'memcmp': ('int', 'const void *, const void *, unsigned'),
    'strcmp': ('int', 'const char *, const char *'),
    'strlen': ('unsigned', 'const char *'),
    'fputs': ('int', 'const char *, void *'),
    'calloc': ('void *', 'unsigned, unsigned'),
    'free': ('void', 'void *'),
    'memmove': ('void *', 'void *, const void *, unsigned'),
    'pow': ('double', 'double, double'), 'cos': ('double', 'double'),
    'sin': ('double', 'double'), 'log': ('double', 'double'),
    'fopen': ('void *', 'const char *, const char *'),
    'fread': ('unsigned', 'void *, unsigned, unsigned, void *'),
    'fwrite': ('unsigned', 'const void *, unsigned, unsigned, void *'),
    'fclose': ('int', 'void *'), 'ferror': ('int', 'void *'),
    'fflush': ('int', 'void *'), 'fileno': ('int', 'void *'), 'fsync': ('int', 'int'),
    'rename': ('int', 'const char *, const char *'), 'unlink': ('int', 'const char *'),
    '__xstat': ('int', 'int, const char *, void *'),
    'access': ('int', 'const char *, int'), 'mkdir': ('int', 'const char *, unsigned'),
    'rmdir': ('int', 'const char *'),  # Apps' Remove (netupdate.c)
    'lseek64': ('long long', 'int, long long, int'), 'read': ('int', 'int, void *, unsigned'),
    'snprintf': ('int', 'char *, unsigned, const char *, ...'),
    '__isoc99_sscanf': ('int', 'const char *, const char *, ...'),  # peq.c peq_number: no strtod in demo's GOT
    'opendir': ('void *', 'const char *'), 'closedir': ('int', 'void *'),
    'readdir': ('struct dirent *', 'void *'),
    'qsort': ('void', 'void *, unsigned, unsigned, int (*)(const void *, const void *)'),
    # libcstl and socket imports for the Play/Pause queue menu in navigation.c
    '_create_deque': ('void *', 'const char *'), 'deque_init': ('void', 'void *'),
    'deque_init_copy': ('void', 'void *, const void *'), 'deque_size': ('unsigned', 'const void *'),
    'deque_at': ('void *', 'const void *, unsigned'), '_deque_push_back': ('void', 'void *, ...'),
    'deque_assign': ('void', 'void *, const void *'), 'deque_clear': ('void', 'void *'),
    'deque_pop_back': ('void', 'void *'),
    'deque_destroy': ('void', 'void *'), 'send': ('int', 'int, const void *, unsigned, int'),
    # Coverflow's art thread (coverflow.c)
    'pthread_create': ('int', 'unsigned long *, const void *, void *(*)(void *), void *'),
    'pthread_join': ('int', 'unsigned long, void **'), 'pthread_self': ('unsigned long', 'void'), 'pthread_detach': ('int', 'unsigned long'), 'pthread_mutex_lock': ('int', 'void *'),
    'sem_wait': ('int', 'void *'), 'sem_post': ('int', 'void *'),  # tidal.c: the idle stream threads park on these
    'pthread_mutex_unlock': ('int', 'void *'), 'statfs': ('int', 'const char *, void *'),
    'strdup': ('char *', 'const char *'),
    'strrchr': ('char *', 'const char *, int'), 'strcasecmp': ('int', 'const char *, const char *'),
    'munmap': ('int', 'void *, unsigned'),
    'strcasestr': ('char *', 'const char *, const char *'),
    'strncmp': ('int', 'const char *, const char *, unsigned'),
    'strncasecmp': ('int', 'const char *, const char *, unsigned'),
    # iPod status bar clock (navigation.c)
    'time': ('long', 'long *'),
    'localtime_r': ('const int *', 'const long *, int *'), 'tzset': ('void', 'void'),
    # Scrobble upload (scrobble.c): demo's libcurl and libcrypto
    'fseek': ('int', 'void *, long, int'), 'ftell': ('long', 'void *'), 'atoi': ('int', 'const char *'),
    'strstr': ('char *', 'const char *, const char *'),
    'curl_easy_init': ('void *', 'void'), 'curl_easy_setopt': ('int', 'void *, int, ...'),
    'curl_easy_perform': ('int', 'void *'), 'curl_easy_getinfo': ('int', 'void *, int, ...'),
    'curl_easy_cleanup': ('void', 'void *'), 'curl_slist_append': ('void *', 'void *, const char *'),
    'curl_slist_free_all': ('void', 'void *'), 'MD5_Init': ('int', 'void *'),
    'MD5_Update': ('int', 'void *, const void *, unsigned'), 'MD5_Final': ('int', 'unsigned char *, void *'),
    'dup2': ('int', 'int, int'),
    '__udivdi3': ('unsigned long long', 'unsigned long long, unsigned long long'),
    # Videos (books.c): q2video started and waited for, and its key socket
    'fork': ('int', 'void'), 'execl': ('int', 'const char *, const char *, ...'), 'exit': ('void', 'int'),
    # Home's Rockbox row (navigation.c rockbox_open)
    'system': ('int', 'const char *'),
    'waitpid': ('int', 'int, int *, int'), '__errno_location': ('int *', 'void'), 'socket': ('int', 'int, int, int'), 'close': ('int', 'int'),
    'connect': ('int', 'int, const void *, unsigned'),
    'recv': ('int', 'int, void *, unsigned, int'),
    'select': ('int', 'int, void *, void *, void *, void *'),
    'getsockopt': ('int', 'int, int, int, void *, unsigned *'),
    'fcntl': ('int', 'int, int, ...'), 'ioctl': ('int', 'int, unsigned, ...'),
    'usleep': ('int', 'unsigned'),
    'sendto': ('int', 'int, const void *, unsigned, int, const void *, unsigned'),
    # The visualizer's PCM tap (peq_player.c writes, visualizer.c reads) and its analysis
    'open': ('int', 'const char *, int, ...'), 'ftruncate': ('int', 'int, long'),
    'mmap': ('void *', 'void *, unsigned, int, int, int, long'),
    'mmap64': ('void *', 'void *, unsigned, int, int, int, long long'),
    'clock_gettime': ('int', 'int, void *'),
    'sinf': ('float', 'float'), 'cosf': ('float', 'float'),
    # The wheel's Quick Settings swipe (playback.c qs_swipe) writes touch events to hyn_ts
    'write': ('int', 'int, const void *, unsigned'),
}

def compile_common(out, binary, player=False, ipod=False, flac_experiments=()):
    got = {}
    for line in run('readelf', '-AW', binary).splitlines():
        words = line.split()
        if len(words) >= 6 and 'UND' in words and re.fullmatch('[0-9a-f]{8}', words[0]):
            got[words[-1].split('@')[0]] = int(words[0], 16)
    gp = 0xb16750 if player else GP
    header = ['struct dirent { unsigned ino, off; unsigned short reclen; unsigned char d_type; char d_name[256]; };']
    asm = ['.set noreorder', '.text']
    for name, (ret, args) in LIBC.items():
        # Unused I/O is removed by --gc-sections in the player payload.
        alias = {'fopen': 'fopen64', 'readdir': 'readdir64', 'pow': '__pow_finite'}.get(name, name) if player else name
        if name == 'localtime_r' and not player:
            libc = symbols(out/'libc-2.28.so')
            delta = libc['localtime_r@@GLIBC_2.0'] - libc['tzset@@GLIBC_2.0']
            check(delta == -7724, 'Unexpected libc localtime_r offset')
            header.append(f'extern {ret} peq_lib_{name}({args});')
            header.append(f'#define {name} peq_lib_{name}')
            address = got['tzset']
            asm += ['.section .scratch,"aw",@nobits', '.align 2',
                    'localtime_r_target: .space 4', f'.section .text.peq_lib_{name},"ax",@progbits',
                    f'.globl peq_lib_{name}', f'peq_lib_{name}:',
                    'lui $t8, %hi(localtime_r_target)', 'lw $t9, %lo(localtime_r_target)($t8)',
                    'bnez $t9, 1f', 'nop',
                    # Resolve tzset's lazy GOT entry once: unlike localtime, it writes no shared buffer.
                    'addiu $sp, $sp, -32', 'sw $ra, 28($sp)', 'sw $a0, 20($sp)', 'sw $a1, 24($sp)',
                    'jal peq_lib_tzset', 'nop', 'lw $ra, 28($sp)', 'lw $a0, 20($sp)',
                    'lw $a1, 24($sp)', 'addiu $sp, $sp, 32',
                    f'lui $t8, {address >> 16}', f'ori $t8, $t8, {address & 65535}',
                    'lw $t9, 0($t8)', f'addiu $t9, $t9, {delta}',
                    'lui $t8, %hi(localtime_r_target)', 'sw $t9, %lo(localtime_r_target)($t8)',
                    '1:', 'jr $t9', 'nop']
            continue
        if alias not in got:
            header.append(f'extern {ret} {name}({args});')
            continue
        address = got[alias]
        header += [f'extern {ret} peq_lib_{name}({args});', f'#define {name} peq_lib_{name}']
        asm += [f'.section .text.peq_lib_{name},"ax",@progbits', f'.globl peq_lib_{name}',
                f'peq_lib_{name}:', f'lui $gp, {gp >> 16}', f'ori $gp, $gp, {gp & 65535}',
                f'lui $t8, {address >> 16}', f'ori $t8, $t8, {address & 65535}',
                'lw $t9, 0($t8)', 'jr $t9', 'nop']
        if name in ('memcpy', 'memset', '__udivdi3'):
            asm += [f'.globl {name}', f'.set {name}, peq_lib_{name}']
    if not player:
        syms = symbols(binary) | PRIVATE_FUNCTIONS
        for name, (ret, args) in FUNCTIONS.items():
            if name in syms and syms[name]:
                header.append(f'#define {name} (({ret} (*)({args}))0x{syms[name]:x}u)')
        # build.py has already checked each is a one-byte global.
        header += [f'#define {name} (*(volatile unsigned char *)0x{syms[name]:x}u)' for name in GLOBALS]
    else:
        # filter_n_bytes keeps sh_audio in $fp; af_play's two arguments stay intact.
        asm += ['.section .text.mp3_filter_entry,"ax",@progbits', '.globl mp3_filter_entry',
                'mp3_filter_entry:', 'move $a2, $fp', 'j mp3_filter', 'nop']
    (out/'peq_platform.h').write_text('\n'.join(header)+'\n')
    (out/'peq_imports.S').write_text('\n'.join(asm)+'\n')
    flags = [*FLAGS, '-fno-math-errno', '-ffunction-sections', '-fdata-sections', f'-DIPOD={int(ipod)}']
    if player: flags += ['-mnan=2008']
    objects = []
    for name in (['peq.c', 'peq_player.c', 'mp3_gapless.c'] + [f'flac_{n}.c' for n in flac_experiments]) if player else ['peq.c', 'peq_ui.c', 'coverflow.c', 'library_scope.c', 'scrobble.c', 'photos.c', 'books.c', 'games.c', 'visualizer.c', 'playback.c', 'upnext.c', 'spotify.c', 'addon.c', 'radio.c', 'tidal.c', 'netupdate.c', 'onlinematch.c', 'crossfade.c', 'headset.c', 'lockscreen.c', 'chapters.c', 'lyrics.c']:
        obj = out/(name+'.o')
        run('clang', *flags, '-I', out, '-c', ROOT/'patch'/name, '-o', obj)
        objects.append(obj)
    obj = out/'peq_imports.o'
    run('clang', *flags, '-c', out/'peq_imports.S', '-o', obj)
    return [*objects, obj]

def patch_player(raw, out, flac_experiments=()):
    from flac import selection, EXPORTS, patch_words
    flac_experiments = selection(flac_experiments)
    check(sha(raw) == PLAYER_SHA, 'Unexpected hciplayer')
    out.mkdir(exist_ok=True)
    binary = out/'stock-hciplayer'
    binary.write_bytes(raw)
    check(raw[fileoff(raw, EQ_DESC):fileoff(raw, EQ_DESC)+24].hex() ==
          'ac3d8900cc6388003c3b890098c988000100000024014500', 'EQ descriptor mismatch')
    objects = compile_common(out, binary, True, flac_experiments=flac_experiments)
    script = out/'link.ld'
    script.write_text('SECTIONS { . = 0xe10000; .text : { *(.text*) } .rodata : { *(.rodata*) } '
                      '.data : { *(.data*) *(.sdata*) } .bss : { *(.bss*) *(.sbss*) } '
                      '__end = .; /DISCARD/ : { *(.comment) *(.note*) *(.pdr) *(.mdebug*) '
                      '*(.MIPS.abiflags) *(.reginfo) } }')
    run('ld.lld', '-m', 'elf32ltsmip', '--gc-sections', '-T', script, '-e', 'peq_open', '--undefined=mp3_seek', '--undefined=id3_gain',
        '--undefined=mp3_private_alloc', '--undefined=mp3_filter_entry', '--undefined=mp3_close', '--undefined=mp3_read',
        '--undefined=hci_idle_wait',
        '--undefined=hci_pause_wait', '--undefined=hci_command', '--undefined=rs_open',
        *(f'--undefined={symbol}' for n in flac_experiments for symbol in EXPORTS[n]),
        *objects, '-o', out/'peq.elf')
    run('llvm-objcopy', '-O', 'binary', out/'peq.elf', out/'peq.bin')
    ps = symbols(out/'peq.elf')
    payload = (out/'peq.bin').read_bytes()
    data = bytearray(raw)
    off = fileoff(raw, EQ_OPEN_SLOT)
    data[off:off+4] = struct.pack('<I', ps['peq_open'])
    patch_word(data, VBR_SCAN, 0x144008a8, 0, 'VBR scan')  # bnez $v0, 0x491de0 -> nop
    patch_word(data, *PAUSE_WAIT, jal(ps['hci_pause_wait']), 'pause loop waits for a command')
    patch_word(data, *CMD_DONE, jal(ps['hci_command']), 'a command wakes the idle and pause loops')
    patch_word(data, *IDLE_WAIT, jal(ps['hci_idle_wait']), 'idle loop waits on the play flag')
    for address, stock, patched in EQ_RATE_GATES:
        patch_word(data, address, stock, patched, 'Equalizer rate gate')
    for address, stock, patched in (*MQA_GATE, *MQA_PROBE):
        patch_word(data, address, stock, patched, 'MQA side chain')
    patch_word(data, SEEK_SLOT, STOCK_SEEK, ps['mp3_seek'], 'Audio demuxer seek slot')
    patch_word(data, RS_OPEN_SLOT, RS_OPEN, ps['rs_open'], 'Resampler open slot')
    patch_word(data, MP3_ALLOC, jal(0xadaf00), jal(ps['mp3_private_alloc']), 'MP3 gapless private state')
    patch_word(data, MP3_FILTER, jal(0x44d350), jal(ps['mp3_filter_entry']), 'MP3 gapless PCM')
    patch_word(data, MP3_CLOSE, 0x48b7e4, ps['mp3_close'], 'MP3 gapless cleanup')
    patch_word(data, MP3_READ, jal(DEMUX_READ), jal(ps['mp3_read']), 'MP3 last frame')
    for address in ID3_CALLS:
        patch_word(data, address, jal(STOCK_ID3), jal(ps['id3_gain']), 'MP3 ReplayGain')
    for address, stock, patched in patch_words(flac_experiments, ps):
        patch_word(data, address, stock, patched, 'Experimental FLAC')
    append_payload(data, payload, PLAYER_BASE, max(len(payload), ps['__end']-PLAYER_BASE), 'player')
    (out/'hciplayer').write_bytes(data)
    return dict(stock_sha256=PLAYER_SHA, sha256=sha(data), payload_sha256=sha(payload),
                **({'flac_experiments': list(flac_experiments)} if flac_experiments else {}))
