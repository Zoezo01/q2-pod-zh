#!/usr/bin/env python3
"""Build option guard; optionally verify packaged default and experimental modules."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
from build import build
from rotator import MODULE_PATH, STOCK_SHA256, patch_module

with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    try:
        build(root/'missing.zip', root/'output', root/'missing.jpg', rotator_lazy_clock=True)
    except ValueError as exc:
        assert str(exc) == '--rotator-lazy-clock requires --dev'
    else:
        raise AssertionError('Clock experiment accepted without --dev')
    assert not (root/'output').exists(), 'Guard must run before side effects'

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('builds', nargs='*', type=pathlib.Path)
args = ap.parse_args()
for directory in args.builds:
    manifest = json.loads((directory/'manifest.json').read_text())
    def cat(image):
        return subprocess.check_output(['unsquashfs', '-cat', str(directory/image), MODULE_PATH])
    original = cat('stock.squashfs')
    actual = cat('rootfs.squashfs')
    assert hashlib.sha256(original).hexdigest() == STOCK_SHA256
    if 'rotator_clock' in manifest:
        assert manifest['dev']
        assert actual == patch_module(original) == (directory/'soc_rotator.ko').read_bytes()
        assert manifest['rotator_clock'] == dict(policy='lazy-first-use-v1', path=MODULE_PATH,
            original_sha256=STOCK_SHA256, sha256=hashlib.sha256(actual).hexdigest())
    else:
        assert actual == original, 'Default build changed the module'
    assert (directory/'rootfs.squashfs').stat().st_size <= (directory/'stock.squashfs').stat().st_size
    print(directory.name, 'module packaging passed')
print('Development-only flag rejected before output/counter/input access; PASS')
