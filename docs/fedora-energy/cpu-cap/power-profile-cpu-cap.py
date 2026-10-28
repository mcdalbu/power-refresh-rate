#!/usr/bin/python3
"""Apply CPU caps and optional native Intel Xe GPU policy by power profile."""
import argparse
import logging
import os
from pathlib import Path
import signal
import re
import time

LOG = logging.getLogger('power-profile-cpu-cap')
CPUFREQ = Path('/sys/devices/system/cpu/cpufreq')
POWER = 'org.freedesktop.UPower.PowerProfiles'
POWER_PATH = '/org/freedesktop/UPower/PowerProfiles'


def cpu_list(text):
    result = set()
    for group in re.split(r'[\s,]+', text.strip()):
        if not group:
            continue
        bounds = group.split('-')
        first, last = int(bounds[0]), int(bounds[-1])
        if len(bounds) > 2 or first > last:
            raise ValueError(f'Invalid CPU list: {text}')
        result.update(range(first, last + 1))
    return result


class CpuLimits:
    def __init__(self, root=CPUFREQ, cap_khz=3_000_000,
                 efficiency_cap_khz=2_000_000, core_types=None):
        if cap_khz <= 0 or efficiency_cap_khz <= 0:
            raise ValueError('Frequency cap must be positive')
        self.root = root
        self.cap_khz = cap_khz
        self.efficiency_cap_khz = efficiency_cap_khz
        self.core_types = core_types

    def types(self):
        if self.core_types is not None:
            return self.core_types
        base = Path('/sys/bus/event_source/devices')
        p = cpu_list((base / 'cpu_core/cpus').read_text())
        e = cpu_list((base / 'cpu_atom/cpus').read_text())
        if not p or not e or p & e:
            raise RuntimeError('Cannot identify disjoint Intel P-core and E-core CPU lists')
        return p, e

    def apply(self, profile):
        if profile not in ('power-saver', 'balanced', 'performance'):
            raise ValueError(f'Unknown profile: {profile}')
        plan = []
        p_cores, e_cores = self.types() if profile == 'power-saver' else (set(), set())
        for policy in sorted(self.root.glob('policy[0-9]*')):
            try:
                maximum = int((policy / 'cpuinfo_max_freq').read_text())
                minimum = int((policy / 'scaling_min_freq').read_text())
                current = int((policy / 'scaling_max_freq').read_text())
                related = cpu_list((policy / 'related_cpus').read_text())
            except FileNotFoundError:
                continue  # A CPU may have gone offline during enumeration.
            target = maximum
            if profile == 'power-saver':
                if related and related <= e_cores:
                    target = min(maximum, self.efficiency_cap_khz)
                elif related and related <= p_cores:
                    target = min(maximum, self.cap_khz)
                else:
                    raise RuntimeError(f'{policy.name}: core type is unknown; refusing an ambiguous cap')
            if minimum > target:
                raise RuntimeError(f'{policy.name}: minimum {minimum} kHz exceeds cap {target}; '
                                   'another policy must lower the minimum first')
            plan.append((policy, current, target))
        if not plan:
            raise RuntimeError('No active CPUFreq policies found')
        changed = []
        for policy, current, target in plan:
            try:
                if current != target:
                    (policy / 'scaling_max_freq').write_text(str(target))
                    changed.append(policy.name)
            except FileNotFoundError:
                continue
        # Frequency QoS updates are processed asynchronously by CPUFreq. Submit
        # all writes before checking; an immediate read can still show old limits.
        deadline = time.monotonic() + 2
        while True:
            pending = []
            for policy, _current, target in plan:
                try:
                    actual = int((policy / 'scaling_max_freq').read_text())
                except FileNotFoundError:
                    continue
                if (profile == 'power-saver' and actual > target) or (profile != 'power-saver' and actual != target):
                    pending.append(f'{policy.name}={actual}, expected {target}')
            if not pending:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('CPUFreq did not confirm limits: ' + '; '.join(pending))
            time.sleep(0.02)
        return changed, len(plan)


class XeGpuPolicy:
    def __init__(self, root=Path('/sys/bus/pci/drivers/xe')):
        self.root = root

    @staticmethod
    def target(profile, on_battery=False):
        if profile not in ('power-saver', 'balanced', 'performance'):
            raise ValueError(f'Unknown profile: {profile}')
        return 'power_saving' if on_battery and profile != 'performance' else 'base'

    def apply(self, profile, on_battery=False):
        target = self.target(profile, on_battery)
        changed = []
        for path in sorted(self.root.glob('????:??:??.?/tile*/gt*/freq0/power_profile')):
            try:
                current = path.read_text()
                if f'[{target}]' in current:
                    continue
                if target not in current.replace('[', '').replace(']', '').split():
                    raise RuntimeError(f'{path}: unsupported GPU policy {target}')
                path.write_text(target)
                if f'[{target}]' not in path.read_text():
                    raise RuntimeError(f'{path}: GPU policy did not confirm {target}')
                changed.append(str(path))
            except FileNotFoundError:
                continue  # Device removed during enumeration.
        return changed


class ProfileWatcher:
    def __init__(self, limits, gpu=None):
        import gi
        gi.require_version('Gio', '2.0')
        gi.require_version('GLibUnix', '2.0')
        from gi.repository import Gio, GLib, GLibUnix
        self.Gio, self.GLib, self.GLibUnix = Gio, GLib, GLibUnix
        self.limits = limits
        self.gpu = gpu
        self.last_gpu_error = None
        self.loop = GLib.MainLoop()
        self.pending = 0
        self.last_state = None
        self.last_error = None
        flags = Gio.DBusProxyFlags.DO_NOT_AUTO_START | Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES
        self.power = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SYSTEM, flags, None, POWER, POWER_PATH, POWER, None)
        self.power.connect('g-properties-changed', self.properties_changed)
        self.power.connect('notify::g-name-owner', self.queue)
        self.upower = None
        if self.gpu is not None:
            self.upower = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, flags, None, 'org.freedesktop.UPower',
                '/org/freedesktop/UPower', 'org.freedesktop.UPower', None)
            self.upower.connect('g-properties-changed', self.queue)
            self.upower.connect('notify::g-name-owner', self.queue)
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.sleep_signal = self.bus.signal_subscribe(
            'org.freedesktop.login1', 'org.freedesktop.login1.Manager',
            'PrepareForSleep', '/org/freedesktop/login1', None,
            Gio.DBusSignalFlags.NONE, self.sleep_changed)
        self.monitor = Gio.File.new_for_path(str(CPUFREQ)).monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect('changed', self.queue)

    def queue(self, *_args):
        if self.pending:
            self.GLib.source_remove(self.pending)
        self.pending = self.GLib.timeout_add(100, self.reconcile)

    def properties_changed(self, _proxy, changed, invalidated):
        if 'ActiveProfile' in changed.unpack() or 'ActiveProfile' in invalidated:
            self.queue()

    def sleep_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        if not parameters.unpack()[0]:
            self.queue()

    def reconcile(self):
        self.pending = 0
        try:
            value = self.power.get_cached_property('ActiveProfile')
            if not self.power.get_name_owner() or value is None:
                raise RuntimeError('Waiting for power-profiles-daemon; keeping current clock limits')
            profile = value.unpack()
            changed, count = self.limits.apply(profile)
            if changed or self.last_state != profile:
                rule = (f'P-cores <= {self.limits.cap_khz} kHz, E-cores <= '
                        f'{self.limits.efficiency_cap_khz} kHz') if profile == 'power-saver' else 'hardware maxima'
                LOG.info('%s: %s on %d CPU policies (%d updated)', profile, rule, count, len(changed))
            self.last_state = profile
            self.last_error = None
            # A GPU failure must not prevent CPU caps from being reconciled.
            if self.gpu is not None:
                try:
                    battery = self.upower.get_cached_property('OnBattery')
                    if not self.upower.get_name_owner() or battery is None:
                        raise RuntimeError('Waiting for UPower; keeping current GPU policy')
                    on_battery = battery.unpack()
                    if self.gpu.apply(profile, on_battery):
                        LOG.info('%s/%s: Xe GPU policy %s', profile,
                                 'battery' if on_battery else 'ac',
                                 self.gpu.target(profile, on_battery))
                    self.last_gpu_error = None
                except (OSError, RuntimeError) as error:
                    if str(error) != self.last_gpu_error:
                        LOG.error('Xe GPU: %s', error)
                    self.last_gpu_error = str(error)
        except (OSError, ValueError, RuntimeError) as error:
            message = str(error)
            if message != self.last_error:
                LOG.error('%s', message)
            self.last_error = message
        return self.GLib.SOURCE_REMOVE

    def check(self):
        # Fallback for kernel hotplug notifications and competing limit writes.
        # Only read on healthy checks; no sysfs writes when limits already match.
        self.queue()
        return self.GLib.SOURCE_CONTINUE

    def stop(self):
        self.loop.quit()
        return self.GLib.SOURCE_REMOVE

    def run(self):
        self.GLibUnix.signal_add(self.GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.stop)
        self.GLibUnix.signal_add(self.GLib.PRIORITY_DEFAULT, signal.SIGINT, self.stop)
        self.GLib.timeout_add_seconds(5, self.check)
        self.queue()
        self.loop.run()
        self.monitor.cancel()
        self.bus.signal_unsubscribe(self.sleep_signal)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-khz', type=int, default=3_000_000)
    parser.add_argument('--efficiency-max-khz', type=int, default=2_000_000)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--xe-gpu-policy', action='store_true',
                        help='Use Xe power_saving on battery except in performance')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    if os.geteuid() != 0:
        parser.error('System CPU limits require administrative privileges')
    limits = CpuLimits(cap_khz=args.max_khz, efficiency_cap_khz=args.efficiency_max_khz)
    gpu = XeGpuPolicy() if args.xe_gpu_policy else None
    if args.restore:
        changed, count = limits.apply('balanced')
        LOG.info('Restored hardware maxima on %d policies (%d updated)', count, len(changed))
        if gpu is not None:
            gpu.apply('balanced')
    else:
        ProfileWatcher(limits, gpu).run()


if __name__ == '__main__':
    main()
