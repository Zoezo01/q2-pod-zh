"""Opt-in, checked FLAC experiments for the pinned V1.34 player only."""

EXPERIMENTS = ('sync', 'lpc', 'output')
EXPORTS = {
    'sync': ('flac_sync_check',),
    'lpc': ('flac_lpc32', 'flac_lpc64'),
    'output': ('flac_output_s16i', 'flac_output_s32i', 'flac_output_s16p', 'flac_output_s32p'),
}
SYNC_CALLS = (0x5b69c4, 0x5b6a78, 0x5b6c60, 0x5b6cc0, 0x5b6d30)
# (export, lui address/stock word, addiu address/stock word). Preserve the
# original function entries, which remain callable fallbacks.
POINTERS = {
    'lpc': (
        ('flac_lpc32', 0x40a1dc, 0x3c02005c, 0x40a1e4, 0x2442aedc),
        ('flac_lpc64', 0x40a1ec, 0x3c02005c, 0x40a1f0, 0x2442b05c),
    ),
    'output': (
        ('flac_output_s16i', 0x40a2b4, 0x3c02005c, 0x40a2b8, 0x244283a0),
        ('flac_output_s32i', 0x40a24c, 0x3c02005c, 0x40a250, 0x24429238),
        ('flac_output_s16p', 0x40a2e8, 0x3c02005c, 0x40a2ec, 0x244290c0),
        ('flac_output_s32p', 0x40a280, 0x3c02005c, 0x40a284, 0x2442ad5c),
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
            words.extend((a, 0x0c10adc4, jal(symbols['flac_sync_check'])) for a in SYNC_CALLS)
        else:
            for symbol, hi, old_hi, lo, old_lo in POINTERS[name]:
                target = symbols[symbol]
                words.extend(((hi, old_hi, 0x3c020000 | (target >> 16)),
                              (lo, old_lo, 0x34420000 | (target & 0xffff))))
    return tuple(words)
