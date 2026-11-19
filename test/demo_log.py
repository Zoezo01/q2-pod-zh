"""Generated release/dev logging and collector under sh, or Q2_LOG_SHELL's target BusyBox.

Mount is a stand-in here; real tmpfs saturation is a separate integration check. No device access.
"""
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
from build import BOOT_HOOK, LOG_HOOK, demo_log
import q2remote


def check():
    shell = shlex.split(os.environ.get('Q2_LOG_SHELL', 'sh'))
    assert BOOT_HOOK[1].count(LOG_HOOK[0]) == 1
    for dev in (False, True):
        suffix, threshold = ('log', 3670016) if dev else ('err', 458752)
        for failure in ('none', 'directory', 'symlink', 'mount', 'init', 'alias-file', 'alias-dir',
                        'alias-link', 'link', 'verify', 'repeat', 'trim', 'threshold', 'above'):
            with tempfile.TemporaryDirectory(prefix='q2-log-') as tmp:
                root = pathlib.Path(tmp)
                buf, alias = root/'q2-logbuf', root/f'q2-demo.{suffix}'
                data, events = root/'data', root/'events'
                data.mkdir()
                if failure == 'directory': buf.mkdir()
                if failure == 'symlink': buf.symlink_to(data, target_is_directory=True)
                if failure == 'alias-file': alias.write_text('STALE LOG\n')
                if failure == 'alias-dir': alias.mkdir()
                if failure == 'alias-link': alias.symlink_to(data/'missing')
                script = demo_log(dev).decode()
                # Synchronous single-pass trim makes failure/threshold checks deterministic.
                script = script.replace('done) &', 'done)')
                if failure == 'trim':
                    script = script.replace('( : >"$log" )', '( : >"$log"/missing )')
                script += '            s=$?; break\n        done\n'
                collector = BOOT_HOOK[1].decode().split('        # 0 and 143', 1)[1].rsplit('    ) &', 1)[0]
                script += '        # 0 and 143' + collector
                if failure == 'repeat':
                    script += script  # exact setup twice: second must fall back, with no extra mount/trimmer
                script += '\necho "SURVIVED $log_status destination=$log"\n'
                script = script.replace('/tmp/q2-logbuf', str(buf)).replace(f'/tmp/q2-demo.{suffix}', str(alias))
                script = script.replace('/release/bin/demo', 'demo').replace('/mnt/data', str(data))
                init = 'return 1' if failure == 'mount' else f'command mkdir {buf}/demo.{suffix}' if failure == 'init' else ':'
                trim = failure in ('trim', 'threshold', 'above')
                count = threshold if failure == 'threshold' else threshold + 1
                prelude = f'''c={data}/crash; m={data}/state
mount() {{ echo mount >>{events}; {init}; }}
ln() {{ {'return 1' if failure == 'link' else 'command ln "$@"'}; }}
readlink() {{ {'echo wrong' if failure == 'verify' else 'command readlink "$@"'}; }}
sleep() {{
    echo sleep:$1 >>{events}
    {'if [ ! -e ' + str(root/'tick') + ' ]; then touch ' + str(root/'tick') + '; return 0; fi' if trim else ':'}
    return 1
}}
wc() {{
    {'echo ' + str(count) if failure == 'trim' else 'command wc "$@"'}
}}
demo() {{ echo launched >>{events}; echo captured-stderr >&2; echo captured-stdout; return 137; }}
dmesg() {{ :; }}
ps() {{ :; }}
df() {{ echo filesystem-usage; }}
'''
                if failure in ('threshold', 'above'):
                    # Fill after initialization, before the real generated trim loop reads the size.
                    script = script.replace('log_status=enabled', f'log_status=enabled\nhead -c {count} /dev/zero >"$log"')
                result = subprocess.run([*shell, '-c', prelude + script], capture_output=True, timeout=10)
                assert result.returncode == 0, (dev, failure, result.stderr)
                output = result.stdout.decode()
                event = events.read_text()
                enabled = failure in ('none', 'trim', 'threshold', 'above')
                assert f'SURVIVED {"enabled" if enabled else "disabled"} destination={buf if enabled else "/dev/null"}' in output, (dev, failure, output)
                assert event.count('launched') == (2 if failure == 'repeat' else 1), (dev, failure, event)
                assert event.count('mount') <= 1 and event.count('sleep:60') <= (2 if trim else 1), (dev, failure, event)
                crash = (data/'crash').read_text()
                assert 'STALE LOG' not in crash
                if enabled:
                    assert 'enabled destination=' in crash and 'filesystem-usage' in crash
                    if failure != 'trim':
                        log = buf/f'demo.{suffix}'
                        assert alias.is_symlink() and alias.resolve() == log
                        assert b'captured-stderr' in log.read_bytes()
                        assert (b'captured-stdout' in log.read_bytes()) == dev
                        if failure == 'above': assert log.stat().st_size < 100
                        if failure == 'threshold': assert log.stat().st_size > threshold
                else:
                    assert 'disabled destination=/dev/null' in crash and '== stderr' not in crash and '== q2-demo.log' not in crash
                print(f'{"dev" if dev else "release"} {failure}: startup survived, selected destination and collector checked')


def remote_clear_check():
    """Execute the command the client sends, without a connection to a device."""
    shell = shlex.split(os.environ.get('Q2_LOG_SHELL', 'sh'))
    for case in ('missing', 'regular', 'alias', 'unexpected', 'dangling', 'directory'):
        with tempfile.TemporaryDirectory(prefix='q2-log-clear-') as tmp:
            root = pathlib.Path(tmp)
            alias, bounded, other = root/'q2-demo.log', root/'demo.log', root/'other'
            bounded.write_text('bounded\n'); other.write_text('preserved\n')
            if case == 'regular': alias.write_text('old firmware\n')
            if case == 'alias': alias.symlink_to(bounded)
            if case == 'unexpected': alias.symlink_to(other)
            if case == 'dangling': alias.symlink_to(root/'absent')
            if case == 'directory': alias.mkdir()
            original_log, original_shell = q2remote.LOG, q2remote.shell
            commands = []
            def run(command):
                command = command.replace('/tmp/q2-logbuf/demo.log', str(bounded))
                result = subprocess.run([*shell, '-c', command], capture_output=True, timeout=10)
                commands.append(result.returncode)
                return b''
            try:
                q2remote.LOG, q2remote.shell = str(alias), run
                q2remote.main(['log', '--clear'])
            finally:
                q2remote.LOG, q2remote.shell = original_log, original_shell
            assert commands == [0 if case in ('regular', 'alias') else 1], (case, commands)
            assert other.read_text() == 'preserved\n'
            assert bounded.read_text() == ('' if case == 'alias' else 'bounded\n')
            if case == 'regular': assert alias.read_text() == ''
            if case == 'missing': assert not alias.exists() and not alias.is_symlink()
            if case == 'dangling': assert not (root/'absent').exists()
            print(f'remote clear {case}: checked')


if __name__ == '__main__':
    check()
    remote_clear_check()
