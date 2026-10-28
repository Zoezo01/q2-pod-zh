#!/usr/bin/env python3
"""Check the fixed Simplified Chinese build's font coverage and packaged UI labels."""
import argparse
import io
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from fontTools.ttLib import TTFont
from ipod import HOME_PAGE, decode


def check(directory):
    def asset(rel):
        return subprocess.check_output([
            'unsquashfs', '-cat', str(directory / 'rootfs.squashfs'),
            'release/assets/default/raw/' + rel,
        ])

    with TTFont(io.BytesIO(asset('fonts/default.ttf'))) as font:
        cmap = font.getBestCmap()
    # Include literals and comments conservatively: all Han/fullwidth characters in the
    # payload and UI builder must exist in the actual packaged font, not a host fallback.
    chars = set()
    for path in [*sorted((ROOT / 'patch').glob('*.c')), ROOT / 'tools/ipod.py']:
        chars.update(re.findall(r'[\u3400-\u9fff\uff00-\uffef]', path.read_text()))
    missing = sorted(c for c in chars if ord(c) not in cmap)
    assert not missing, f'Missing font glyphs: {"".join(missing)}'
    # onlinematch.c's fill-row spinner (lf_spinner) turns through four quarter circles: its own
    # characters, outside the Han range the sweep above keeps.
    spinner = '\u25d0\u25d3\u25d1\u25d2'
    missing = [c for c in spinner if ord(c) not in cmap]
    assert not missing, f'Missing spinner glyphs: {"".join(missing)}'

    home = decode(asset('ui/' + HOME_PAGE))

    def walk(node):
        yield node
        for child in node[3]:
            yield from walk(child)

    labels = {node[2].get('name'): node[2].get('text') for node in walk(home)}
    for name, text in [('label_library', '音乐'), ('label_coverflow', 'Cover Flow'),
                       ('label_settings', '设置'), ('label_playback', '播放设置'),
                       ('label_system', '系统设置')]:
        assert labels.get(name) == text, (name, labels.get(name))

    dialog = decode(asset('ui/dialog/confirminfo_dialog.bin'))
    assert [node[2]['text'] for node in dialog[3]] == ['取消', '确定']
    payload = (directory / 'patch.bin').read_bytes()
    for label in ('评价', '播放最多', '低功耗模式：打开', '音量防误触：打开',
                  '随机播放歌曲', '查看队列', '网络电台', '待机时限：关闭',
                  '单声道音频', '断点续播：关闭', '上一页', '下一页',
                  '左右声道互换', '章节', '前往文件夹', '编辑播放列表',
                  '上游英文固件更新', '应用商店', 'Q2 应用商店', '下载管理',
                  '存储管理', '已下载歌曲', '移除下载？', '年代', '数独', '贪吃蛇'):
        assert label.encode() + b'\0' in payload, label
    # The localized labels must never replace the names of SD card directories.
    for folder in ('Podcasts', 'Audiobooks', 'Photos', 'Books', 'Videos'):
        assert folder.encode() + b'\0' in payload, folder
    print(f'Chinese build checks passed: {len(chars)} glyphs, Home, confirmation, '
          'payload labels and original media directory names.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build', type=pathlib.Path)
    check(parser.parse_args().build)
