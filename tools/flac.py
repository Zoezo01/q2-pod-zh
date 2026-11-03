"""Opt-in, checked FLAC experiments for the pinned V1.32 player only."""

EXPERIMENTS = ('sync', 'lpc', 'output')
EXPORTS = {
    'sync': ('flac_sync_check',),
    'lpc': ('flac_lpc32', 'flac_lpc64'),
    'output': ('flac_output_s16i', 'flac_output_s32i', 'flac_output_s16p', 'flac_output_s32p'),
}
SYNC_CALLS = (0x5b64a4, 0x5b6558, 0x5b6740, 0x5b67a0, 0x5b6810)
# (export, lui address/stock word, addiu address/stock word). Preserve the
# original function entries, which remain callable fallbacks.
POINTERS = {
    'lpc': (
        ('flac_lpc32', 0x40a17c, 0x3c02005c, 0x40a184, 0x2442a9bc),
        ('flac_lpc64', 0x40a18c, 0x3c02005c, 0x40a190, 0x2442ab3c),
    ),
    'output': (
        ('flac_output_s16i', 0x40a254, 0x3c02005b, 0x40a258, 0x24427e80),
        ('flac_output_s32i', 0x40a1ec, 0x3c02005c, 0x40a1f0, 0x24428d18),
        ('flac_output_s16p', 0x40a288, 0x3c02005c, 0x40a28c, 0x24428ba0),
        ('flac_output_s32p', 0x40a220, 0x3c02005c, 0x40a224, 0x2442a83c),
    ),
}


def selection(value):
    names = value.split(',') if isinstance(value, str) and value else tuple(value)
    if len(set(names)) != len(names) or set(names) - set(EXPERIMENTS):
        raise ValueError('FLAC experiments must be a comma-separated subset of sync,lpc,output without duplicates')
    return tuple(name for name in EXPERIMENTS if name in names)


def patch_words(experiments, symbols):
    from build import jal
    words = []
    for name in selection(experiments):
        if name == 'sync':
            words.extend((a, 0x0c10adac, jal(symbols['flac_sync_check'])) for a in SYNC_CALLS)
        else:
            for symbol, hi, old_hi, lo, old_lo in POINTERS[name]:
                target = symbols[symbol]
                words.extend(((hi, old_hi, 0x3c020000 | (target >> 16)),
                              (lo, old_lo, 0x34420000 | (target & 0xffff))))
    return tuple(words)
