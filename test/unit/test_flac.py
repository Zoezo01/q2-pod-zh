"""tools/flac.py: which experiments run, and the words they patch."""
import pytest

import build
import flac

SYMBOLS = {'flac_sync_check': 0x00b01234, 'flac_lpc32': 0x00b12340, 'flac_lpc64': 0x00b22340,
           'flac_output_s16i': 0x00b30000, 'flac_output_s32i': 0x00b40000,
           'flac_output_s16p': 0x00b50000, 'flac_output_s32p': 0x00b60000}


@pytest.mark.parametrize('value,expected', [
    ('', ()),
    ((), ()),
    ('sync', ('sync',)),
    ('output,sync', ('sync', 'output')),
    ('sync,lpc,output', ('sync', 'lpc', 'output')),
    (['lpc', 'sync'], ('sync', 'lpc')),
])
def test_selection(value, expected):
    assert flac.selection(value) == expected


@pytest.mark.parametrize('value', ['sync,sync', 'other', 'sync,other', ['sync', 'sync'], ['other'], 'sync,'])
def test_selection_refuses(value):
    with pytest.raises(ValueError, match=r'^FLAC experiments must be a comma-separated subset of sync,lpc,output without duplicates$'):
        flac.selection(value)


def test_patch_words_none():
    assert flac.patch_words('', {}) == ()


def test_patch_words_sync():
    assert flac.patch_words('sync', SYMBOLS) == tuple(
        (site, 0x0c10adac, build.jal(0xb01234)) for site in (0x5b64a4, 0x5b6558, 0x5b6740, 0x5b67a0, 0x5b6810))


def test_patch_words_lpc():
    assert flac.patch_words('lpc', SYMBOLS) == (
        (0x40a17c, 0x3c02005c, 0x3c0200b1), (0x40a184, 0x2442a9bc, 0x34422340),
        (0x40a18c, 0x3c02005c, 0x3c0200b2), (0x40a190, 0x2442ab3c, 0x34422340))


def test_patch_words_output_order():
    words = flac.patch_words('output', SYMBOLS)
    assert [w[0] for w in words] == [0x40a254, 0x40a258, 0x40a1ec, 0x40a1f0, 0x40a288, 0x40a28c, 0x40a220, 0x40a224]
    assert words[0][2] == 0x3c0200b3 and words[-1][2] == 0x34420000


def test_patch_words_all_three_in_order():
    words = flac.patch_words('output,lpc,sync', SYMBOLS)
    assert len(words) == 5 + 4 + 8 and words[0][0] == 0x5b64a4 and words[5][0] == 0x40a17c and words[9][0] == 0x40a254
