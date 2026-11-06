#!/usr/bin/python3
"""Upgrade an installed backend without changing any CPU/GPU/EPP settings."""
from pathlib import Path
import fcntl
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
SCRIPT = Path('/usr/local/libexec/power-profile-cpu-cap.py')
HELPER = Path('/usr/local/libexec/power-profile-settings-helper')
UNIT = Path('/etc/systemd/system/power-profile-cpu-cap.service')


def install(path, content, mode):
    fd, name = tempfile.mkstemp(prefix='.power-parking-upgrade-', dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        temporary.write_bytes(content)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(*args):
    return subprocess.run(args, check=True)


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Administrator privileges required')
    with open('/run/power-profile-settings.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = {path: path.read_bytes() for path in (SCRIPT, HELPER, UNIT)}
        configs = [Path('/etc/power-profile-cpu-cap/config.json'), Path('/etc/power-profile-lpmd/config.json')]
        before = {path: path.read_bytes() for path in configs}
        backup = Path('/var/backups/energy-trial') / f'p-core-parking-{time.time_ns()}'
        backup.mkdir(mode=0o700, parents=True)
        for path in (*previous, *configs):
            shutil.copy2(path, backup / (path.name if path in previous else path.parent.name + '.json'))
        spec = importlib.util.spec_from_file_location('cpu_backend', ROOT / SCRIPT.name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            # The old controller's stop hook restores any cores it owns first.
            run('systemctl', 'stop', UNIT.name)
            p, _e = module.CpuLimits().types()
            unit = module.parking_unit(previous[UNIT].decode(), p)
            install(SCRIPT, (ROOT / SCRIPT.name).read_bytes(), 0o755)
            install(HELPER, (ROOT / HELPER.name).read_bytes(), 0o755)
            install(UNIT, unit.encode(), 0o644)
            run('restorecon', '-F', str(SCRIPT), str(HELPER), str(UNIT))
            run('systemctl', 'daemon-reload')
            run('systemd-analyze', 'verify', '--man=no', str(UNIT))
            run('systemctl', 'start', UNIT.name)
            run('systemctl', 'is-active', '--quiet', UNIT.name)
            if any(path.read_bytes() != content for path, content in before.items()):
                raise RuntimeError('Configuration changed during upgrade; verify current preferences')
        except BaseException:
            subprocess.run(['systemctl', 'stop', UNIT.name], check=False)
            for path, content in previous.items():
                install(path, content, 0o644 if path == UNIT else 0o755)
            subprocess.run(['restorecon', '-F', *(str(path) for path in previous)], check=False)
            subprocess.run(['systemctl', 'daemon-reload'], check=False)
            subprocess.run(['systemctl', 'start', UNIT.name], check=False)
            raise
        print(json.dumps({'updated': True, 'backup': str(backup), 'settings_preserved': True}))


if __name__ == '__main__':
    main()
