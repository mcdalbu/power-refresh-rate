import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cpu_cap', Path(__file__).with_name('power-profile-cpu-cap.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CpuLimitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for i, maximum in enumerate([4_700_000, 4_800_000, 3_700_000, 2_800_000]):
            p = self.root / f'policy{i}'
            p.mkdir()
            for key, value in {'cpuinfo_max_freq': maximum, 'scaling_max_freq': maximum,
                               'scaling_min_freq': 400_000}.items():
                (p / key).write_text(str(value))
            (p / 'related_cpus').write_text(str(i))
        self.core_types = ({0, 1, 4}, {2, 3})
        self.limits = module.CpuLimits(self.root, core_types=self.core_types)

    def maxima(self):
        return [int((p / 'scaling_max_freq').read_text()) for p in sorted(self.root.iterdir())]

    def test_cap_all_core_types_and_restore(self):
        self.limits.apply('power-saver')
        self.assertEqual(self.maxima(), [3_000_000, 3_000_000, 2_000_000, 2_000_000])
        self.assertEqual(self.limits.apply('power-saver'), ([], 4))
        self.limits.apply('balanced')
        self.assertEqual(self.maxima(), [4_700_000, 4_800_000, 3_700_000, 2_800_000])
        self.limits.apply('power-saver')
        self.limits.apply('performance')
        self.assertEqual(self.maxima(), [4_700_000, 4_800_000, 3_700_000, 2_800_000])

    def test_hotplug_and_restart_reconcile(self):
        self.limits.apply('power-saver')
        new = self.root / 'policy4'
        new.mkdir()
        for key, value in {'cpuinfo_max_freq': 4_800_000, 'scaling_max_freq': 4_800_000,
                           'scaling_min_freq': 400_000}.items():
            (new / key).write_text(str(value))
        (new / 'related_cpus').write_text('4')
        restarted = module.CpuLimits(self.root, core_types=self.core_types)
        restarted.apply('power-saver')
        self.assertEqual(self.maxima()[-1], 3_000_000)
        restarted.apply('balanced')
        self.assertEqual(self.maxima()[-1], 4_800_000)

    def test_invalid_minimum_is_rejected_before_any_writes(self):
        before = self.maxima()
        (self.root / 'policy1/scaling_min_freq').write_text('3500000')
        with self.assertRaises(RuntimeError):
            self.limits.apply('power-saver')
        self.assertEqual(self.maxima(), before)

    def test_unknown_profile_keeps_current_limits(self):
        before = self.maxima()
        with self.assertRaises(ValueError):
            self.limits.apply('unknown')
        self.assertEqual(self.maxima(), before)

    def test_unknown_core_type_does_not_partially_apply(self):
        before = self.maxima()
        (self.root / 'policy1/related_cpus').write_text('99')
        with self.assertRaises(RuntimeError):
            self.limits.apply('power-saver')
        self.assertEqual(self.maxima(), before)

    def test_asynchronous_kernel_readback(self):
        original = Path.read_text
        reads = {'remaining': 0}
        original_write = Path.write_text

        def write(path, text, *args, **kwargs):
            result = original_write(path, text, *args, **kwargs)
            if path.name == 'scaling_max_freq':
                reads['remaining'] = 3
            return result

        def read(path, *args, **kwargs):
            if path.name == 'scaling_max_freq' and reads['remaining']:
                reads['remaining'] -= 1
                return original(path.parent / 'cpuinfo_max_freq')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'write_text', write), patch.object(Path, 'read_text', read):
            self.limits.apply('power-saver')
        self.assertEqual(self.maxima(), [3_000_000, 3_000_000, 2_000_000, 2_000_000])


class XeGpuTests(unittest.TestCase):
    def test_both_gt_domains_follow_profile_without_redundant_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for gt in ('gt0', 'gt1'):
                path = root / f'0000:00:02.0/tile0/{gt}/freq0/power_profile'
                path.parent.mkdir(parents=True)
                path.write_text('[base] power_saving')
                paths.append(path)
            write_original = Path.write_text
            def kernel_write(path, value, *args, **kwargs):
                return write_original(path, f'[{value}] ' +
                                      ('base' if value == 'power_saving' else 'power_saving'))
            policy = module.XeGpuPolicy(root)
            with patch.object(Path, 'write_text', kernel_write):
                for profile, battery, target in (
                    ('power-saver', True, 'power_saving'),
                    ('balanced', True, 'power_saving'),
                    ('performance', True, 'base'),
                    ('power-saver', False, 'base'),
                    ('balanced', False, 'base'),
                    ('performance', False, 'base'),
                    ('balanced', True, 'power_saving'),
                    ('balanced', False, 'base'),
                ):
                    policy.apply(profile, battery)
                    self.assertTrue(all(f'[{target}]' in p.read_text() for p in paths))
                    self.assertEqual(policy.apply(profile, battery), [])
                self.assertTrue(all('[base]' in p.read_text() for p in paths))
            with self.assertRaises(ValueError):
                policy.apply('unknown')

    def test_missing_gpu_is_optional_and_rejected_write_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = module.XeGpuPolicy(root)
            self.assertEqual(policy.apply('power-saver', True), [])
            path = root / '0000:00:02.0/tile0/gt0/freq0/power_profile'
            path.parent.mkdir(parents=True)
            path.write_text('[base] power_saving')
            with patch.object(Path, 'write_text', return_value=0):
                with self.assertRaises(RuntimeError):
                    policy.apply('power-saver', True)

class ConfigurableLimitTests(unittest.TestCase):
    setUp = CpuLimitTests.setUp
    maxima = CpuLimitTests.maxima
    def test_caps_in_balanced_and_one_unlimited_class(self):
        self.limits.apply('balanced', (2_500_000, 1_800_000))
        self.assertEqual(self.maxima(), [2_500_000,2_500_000,1_800_000,1_800_000])
        self.limits.apply('performance', (0,1_900_000))
        self.assertEqual(self.maxima(), [4_700_000,4_800_000,1_900_000,1_900_000])
        self.limits.apply('balanced', (0,0))
        self.assertEqual(self.maxima(), [4_700_000,4_800_000,3_700_000,2_800_000])


if __name__ == '__main__':
    unittest.main()
