#!/usr/bin/python3
"""Exercise the extension in a separate headless GNOME Shell (Fedora 50)."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
from gi.repository import Gio, GLib

root = pathlib.Path(__file__).resolve().parents[1]
uuid = 'power-refresh-rate@mcda.local'
with tempfile.TemporaryDirectory(prefix='power-refresh-shell-') as temporary:
    base = pathlib.Path(temporary)
    target = base / 'data/gnome-shell/extensions' / uuid
    target.mkdir(parents=True)
    for name in ['extension.js', 'controller.js', 'policy.js', 'prefs.js', 'metadata.json']:
        shutil.copy2(root / name, target / name)
    shutil.copytree(root / 'schemas', target / 'schemas')
    env = dict(os.environ, XDG_CONFIG_HOME=str(base / 'config'),
               XDG_DATA_HOME=str(base / 'data'), GSETTINGS_BACKEND='keyfile',
               LIBGL_ALWAYS_SOFTWARE='1')
    for key in ['DISPLAY', 'WAYLAND_DISPLAY', 'LD_PRELOAD', 'POWER_REFRESH_SCALE_SHIM']:
        env.pop(key, None)
    subprocess.run(['gsettings', 'set', 'org.gnome.shell', 'enabled-extensions',
                    "['" + uuid + "']"], env=env, check=True)
    log_path = base / 'gnome-shell.log'
    with log_path.open('w') as log:
        process = subprocess.Popen(['gnome-shell', '--headless', '--wayland', '--no-x11',
            '--mode=user', '--wayland-display=power-refresh-shell-validation',
            '--virtual-monitor=2880x1800@120'], env=env, stdout=log, stderr=log)
        try:
            for attempt in range(150):
                if process.poll() is not None:
                    raise RuntimeError(f'GNOME Shell exited {process.returncode}')
                try:
                    proxy = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION,
                        Gio.DBusProxyFlags.DO_NOT_AUTO_START, None, 'org.gnome.Shell',
                        '/org/gnome/Shell', 'org.gnome.Shell.Extensions', None)
                    info = proxy.call_sync('GetExtensionInfo', GLib.Variant('(s)', (uuid,)),
                                           0, 1000, None).unpack()[0]
                    if info.get('state') == 1:
                        break
                    if info.get('state') in (3, 4):
                        raise RuntimeError(f'Extension failed: {info}')
                except GLib.Error:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError('Extension did not become enabled')
            print('Headless GNOME Shell: extension enabled', flush=True)
            time.sleep(1)
            get_status = ['gsettings', '--schemadir', str(target / 'schemas'), 'get',
                          'org.gnome.shell.extensions.power-refresh-rate', 'last-status']
            status = subprocess.check_output(get_status, env=env, text=True).strip()
            assert 'Sem painel interno ativo.' in status, status
            print('Controller initialized:', status, flush=True)
            for method, expected in [('DisableExtension', 2), ('EnableExtension', 1)]:
                proxy.call_sync(method, GLib.Variant('(s)', (uuid,)), 0, 3000, None)
                info = proxy.call_sync('GetExtensionInfo', GLib.Variant('(s)', (uuid,)),
                                       0, 3000, None).unpack()[0]
                assert info['state'] == expected, info
                print(method, 'OK', flush=True)
            prefs_env = dict(env, WAYLAND_DISPLAY='power-refresh-shell-validation',
                             GDK_BACKEND='wayland', GTK_A11Y='none',
                             GI_TYPELIB_PATH='/usr/lib64/gnome-shell/girepository-1.0',
                             LD_LIBRARY_PATH='/usr/lib64/gnome-shell')
            subprocess.run(['gjs', '-m', str(root / 'tests/test-prefs.js')],
                           env=prefs_env, check=True, timeout=15)
            print('Preferences window constructed: OK', flush=True)
        except Exception:
            log.flush()
            print(log_path.read_text(), flush=True)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
