import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

spec = importlib.util.spec_from_file_location('parking_backend', Path(__file__).with_name('power-profile-cpu-cap.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ParkingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'cpu'
        for cpu in range(8):
            path = self.root / f'cpu{cpu}'
            path.mkdir(parents=True)
            (path / 'online').write_text('1')
        self.state = Path(temp.name) / 'run/parked.json'
        self.types = lambda: ({0, 1, 2, 3}, {4, 5, 6, 7})
        self.parking = m.CoreParking(self.root, self.state, self.types)

    def online(self):
        return [int((self.root / f'cpu{i}/online').read_text()) for i in range(8)]

    def test_only_secondary_p_cores_and_idempotent_restore(self):
        self.assertEqual(self.parking.park(), [1, 2, 3])
        self.assertEqual(self.online(), [1, 0, 0, 0, 1, 1, 1, 1])
        self.assertEqual(self.parking.park(), [])
        self.assertEqual(self.parking.restore(), [1, 2, 3])
        self.assertEqual(self.online(), [1] * 8)
        self.assertEqual(self.parking.restore(), [])

    def test_previously_offline_cpu_is_not_owned_or_restored(self):
        (self.root / 'cpu3/online').write_text('0')
        self.parking.park()
        self.assertEqual(self.parking.managed(), {1, 2})
        self.parking.restore()
        self.assertEqual(self.online(), [1, 1, 1, 0, 1, 1, 1, 1])

    def test_crash_recovery_does_not_depend_on_offline_pmu_lists(self):
        self.parking.park()
        recovered = m.CoreParking(self.root, self.state, Mock(side_effect=RuntimeError('PMU changed')))
        self.assertEqual(recovered.restore(), [1, 2, 3])
        self.assertEqual(self.online(), [1] * 8)

    def test_failed_hotplug_rolls_back_earlier_cores(self):
        original = Path.write_text
        def write(path, value, *args, **kwargs):
            if path == self.root / 'cpu2/online' and value == '0':
                raise OSError('kernel refused')
            return original(path, value, *args, **kwargs)
        with patch.object(Path, 'write_text', write):
            with self.assertRaises(OSError):
                self.parking.park()
        self.assertEqual(self.online(), [1] * 8)
        self.assertEqual(self.parking.managed(), set())

    def test_failed_restore_keeps_state_and_restores_other_cores(self):
        self.parking.park()
        original = Path.write_text
        def write(path, value, *args, **kwargs):
            if path == self.root / 'cpu2/online' and value == '1':
                raise OSError('temporarily unavailable')
            return original(path, value, *args, **kwargs)
        with patch.object(Path, 'write_text', write):
            with self.assertRaises(RuntimeError):
                self.parking.restore()
        self.assertEqual(self.online(), [1, 1, 0, 1, 1, 1, 1, 1])
        self.assertEqual(self.parking.managed(), {2})
        self.assertEqual(self.parking.restore(), [2])

    def test_refuse_parking_without_online_efficiency_cores(self):
        for cpu in range(4, 8):
            (self.root / f'cpu{cpu}/online').write_text('0')
        with self.assertRaises(RuntimeError):
            self.parking.park()
        self.assertEqual(self.online()[:4], [1] * 4)

    def test_invalid_state_cannot_target_boot_cpu(self):
        self.state.parent.mkdir()
        for ids in ([0], [-1], [True], [65536]):
            self.state.write_text(json.dumps(ids))
            with self.assertRaises(RuntimeError):
                self.parking.restore()

    def test_unit_grants_only_secondary_p_files_and_is_idempotent(self):
        unit = '[Unit]\nDescription=Test\n[Service]\nReadWritePaths=/sys/devices/system/cpu/cpufreq\n[Install]\nWantedBy=multi-user.target\n'
        result = m.parking_unit(unit, {0, 1, 2, 3}, self.root)
        self.assertEqual(m.parking_unit(result, {0, 1, 2, 3}, self.root), result)
        for cpu in range(8):
            self.assertEqual(f'ReadWritePaths=-{self.root}/cpu{cpu}/online' in result, cpu in (1, 2, 3))


class ProfileParkingTests(unittest.TestCase):
    def test_context_switch_restores_before_caps_and_parks_after_caps(self):
        watcher = object.__new__(m.ProfileWatcher)
        context = {'profile': 'power-saver', 'battery': True}
        watcher.power = Mock()
        watcher.power.get_cached_property.side_effect = lambda *_: SimpleNamespace(unpack=lambda: context['profile'])
        watcher.upower = Mock()
        watcher.upower.get_cached_property.side_effect = lambda *_: SimpleNamespace(unpack=lambda: context['battery'])
        base = {'p_max_mhz': 2000, 'e_max_mhz': 2000, 'gpu': 'base'}
        watcher.settings = {'power-saver': {'ac': base, 'battery': dict(base, park_p_cores=True)},
                            'balanced': {'ac': base, 'battery': base}, 'performance': {'ac': base, 'battery': base}}
        watcher.limits = Mock()
        watcher.limits.apply.return_value = ([], 5)
        watcher.parking = Mock()
        order = Mock()
        order.attach_mock(watcher.limits.apply, 'caps')
        order.attach_mock(watcher.parking.park, 'park')
        order.attach_mock(watcher.parking.restore, 'restore')
        watcher.gpu = None
        watcher.GLib = SimpleNamespace(SOURCE_REMOVE=False)
        watcher.last_state = watcher.last_error = None
        for profile, battery, expected in [
            ('power-saver', True, [call.caps('power-saver', (2000000, 2000000)), call.park()]),
            ('power-saver', False, [call.restore(), call.caps('power-saver', (2000000, 2000000))]),
            ('balanced', True, [call.restore(), call.caps('balanced', (2000000, 2000000))]),
            ('performance', True, [call.restore(), call.caps('performance', (2000000, 2000000))]),
        ]:
            context.update(profile=profile, battery=battery)
            order.reset_mock()
            watcher.reconcile()
            self.assertEqual(order.mock_calls, expected)
            self.assertIsNone(watcher.last_error)
