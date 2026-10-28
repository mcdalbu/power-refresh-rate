#!/usr/bin/python3
"""Select LPMD workload EPP tables by GNOME power profile and AC/battery."""
import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

LOG = logging.getLogger('power-profile-lpmd')
CONFIG = Path('/etc/power-profile-lpmd')
XML = Path('/etc/intel_lpmd/intel_lpmd_config_F6_M189.xml')
STATES = ('UTIL_IDLE', 'UTIL_IDLE_SUSTAIN', 'UTIL_IDLE_BURSTY', 'UTIL_IDLE_GFX_BUSY')
PROFILES = ('power-saver', 'balanced', 'performance')


def render(template, config, profile, on_battery):
    if profile not in PROFILES:
        raise ValueError(f'Unknown profile: {profile}')
    values = config[profile]['battery' if on_battery else 'ac']
    if len(values) != 4 or any(type(v) is not int or not 0 <= v <= 255 for v in values):
        raise ValueError('Each workload table must contain four EPP integers in 0..255')
    root = ET.fromstring(template)
    root.find('lp_mode_epp').text = '-1'
    root.find('PowersaverDef').text = '0'
    root.find('BalancedDef').text = '0'
    root.find('PerformanceDef').text = '0' if config['performance_dynamic']['battery' if on_battery else 'ac'] else '-1'
    found = set()
    for state in root.findall('./States/State'):
        name = state.findtext('Name').strip()
        if name in STATES:
            state.find('EPP').text = str(values[STATES.index(name)])
            found.add(name)
    if found != set(STATES):
        raise ValueError('Template must contain all four workload states')
    return b'<?xml version="1.0"?>\n' + ET.tostring(root, encoding='utf-8') + b'\n'


def atomic_write(path, content):
    fd, name = tempfile.mkstemp(prefix='.lpmd-profile-', dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copy2(path, temporary)
        temporary.write_bytes(content)
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Selector:
    def __init__(self):
        import gi
        gi.require_version('Gio', '2.0')
        gi.require_version('GLibUnix', '2.0')
        from gi.repository import Gio, GLib, GLibUnix
        self.Gio, self.GLib, self.GLibUnix = Gio, GLib, GLibUnix
        self.template = (CONFIG / 'template.xml').read_bytes()
        self.config = json.loads((CONFIG / 'config.json').read_text())
        # Validate every bank before accepting any changes.
        for profile in PROFILES:
            for battery in (False, True):
                render(self.template, self.config, profile, battery)
        self.loop = GLib.MainLoop()
        flags = Gio.DBusProxyFlags.DO_NOT_AUTO_START | Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES
        self.ppd = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM, flags, None,
            'org.freedesktop.UPower.PowerProfiles', '/org/freedesktop/UPower/PowerProfiles',
            'org.freedesktop.UPower.PowerProfiles', None)
        self.upower = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM, flags, None,
            'org.freedesktop.UPower', '/org/freedesktop/UPower', 'org.freedesktop.UPower', None)
        for proxy in (self.ppd, self.upower):
            proxy.connect('g-properties-changed', self.queue)
            proxy.connect('notify::g-name-owner', self.queue)
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.sleep_signal = self.bus.signal_subscribe('org.freedesktop.login1',
            'org.freedesktop.login1.Manager', 'PrepareForSleep', '/org/freedesktop/login1',
            None, Gio.DBusSignalFlags.NONE, self.sleep_changed)
        self.pending = 0
        self.last_error = None
        self.last_restart = 0
        self.key = None

    def queue(self, *_args):
        if self.pending:
            self.GLib.source_remove(self.pending)
        self.pending = self.GLib.timeout_add(1200, self.reconcile)

    def sleep_changed(self, _bus, _sender, _path, _iface, _signal, args):
        if not args.unpack()[0]:
            self.queue()

    def reconcile(self):
        self.pending = 0
        try:
            if not self.ppd.get_name_owner() or not self.upower.get_name_owner():
                raise RuntimeError('Waiting for PPD/UPower; keeping the last LPMD table')
            profile = self.ppd.get_cached_property('ActiveProfile')
            battery = self.upower.get_cached_property('OnBattery')
            if profile is None or battery is None:
                raise RuntimeError('Waiting for profile and power-source properties')
            profile, battery = profile.unpack(), battery.unpack()
            key = (profile, battery)
            candidate = render(self.template, self.config, profile, battery)
            if XML.read_bytes() == candidate:
                self.key = key
                self.last_error = None
                return self.GLib.SOURCE_REMOVE
            remaining = 3 - (time.monotonic() - self.last_restart)
            if remaining > 0:
                self.pending = self.GLib.timeout_add(int(remaining * 1000) + 1, self.reconcile)
                return self.GLib.SOURCE_REMOVE
            previous = XML.read_bytes()
            atomic_write(XML, candidate)
            try:
                subprocess.run(['/usr/bin/systemctl', 'restart', 'intel_lpmd.service'],
                               check=True, timeout=25)
            except BaseException:
                atomic_write(XML, previous)
                subprocess.run(['/usr/bin/systemctl', 'restart', 'intel_lpmd.service'],
                               check=False, timeout=25)
                raise
            self.last_restart = time.monotonic()
            self.key = key
            self.last_error = None
            LOG.info('%s/%s: workload EPP %s; LPMD %s', profile,
                     'battery' if battery else 'ac', self.config[profile]['battery' if battery else 'ac'],
                     'off' if profile == 'performance' and not self.config['performance_dynamic']['battery' if battery else 'ac'] else 'auto')
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            if str(error) != self.last_error:
                LOG.error('%s', error)
            self.last_error = str(error)
        return self.GLib.SOURCE_REMOVE

    def check(self):
        self.queue()
        return self.GLib.SOURCE_CONTINUE

    def stop(self):
        self.loop.quit()
        return self.GLib.SOURCE_REMOVE

    def run(self):
        import signal
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.GLibUnix.signal_add(self.GLib.PRIORITY_DEFAULT, sig, self.stop)
        self.GLib.timeout_add_seconds(15, self.check)
        self.queue()
        self.loop.run()
        self.bus.signal_unsubscribe(self.sleep_signal)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    if os.geteuid() != 0:
        raise SystemExit('Administrative privileges required')
    Selector().run()
