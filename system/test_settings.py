import copy
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
loader=importlib.machinery.SourceFileLoader('helper',str(Path(__file__).with_name('power-profile-settings-helper')))
spec=importlib.util.spec_from_loader('helper',loader)
m=importlib.util.module_from_spec(spec);loader.exec_module(m)
class SettingsTests(unittest.TestCase):
 def setUp(self):
  base=Path(__file__).parent
  self.data={'version':1,'epp':json.loads((base/'epp-defaults.json').read_text()),'hardware':json.loads((base/'hardware-defaults.json').read_text())}
 def test_accept_current_six_contexts(self):
  m.validate(self.data,{'p_max_mhz':(400,4800),'e_max_mhz':(400,3700)})
 def test_reject_arbitrary_fields_bool_and_limits(self):
  for change in (lambda d:d.update(command='reboot'),lambda d:d['epp']['balanced']['ac'].__setitem__(0,256),lambda d:d['hardware']['balanced']['ac'].__setitem__('p_max_mhz',True),lambda d:d['hardware']['balanced']['ac'].__setitem__('p_max_mhz',300),lambda d:d['hardware']['balanced']['ac'].__setitem__('gpu','unknown'),lambda d:d['epp']['performance_dynamic'].__setitem__('ac','false')):
   d=copy.deepcopy(self.data);change(d)
   with self.assertRaises(ValueError):m.validate(d,{'p_max_mhz':(400,4800),'e_max_mhz':(400,3700)})
 def test_fixed_performance_cannot_hide_nonzero_epp(self):
  self.data['epp']['performance']['ac'][0]=128
  with self.assertRaises(ValueError):m.validate(self.data)
 def test_restart_failure_rolls_back_both_files(self):
  epp,hw=Mock(),Mock()
  for p,kind in ((epp,'epp'),(hw,'hardware')):
   p.is_symlink.return_value=False;p.stat.return_value=SimpleNamespace(st_uid=0,st_mode=0o100644)
   p.read_bytes.return_value=json.dumps(self.data[kind]).encode()
  self.data['epp']['balanced']['battery'][0]=209
  with patch.object(m,'EPP',epp),patch.object(m,'HARDWARE',hw),patch.object(m,'hardware_limits',return_value=None),patch.object(m,'atomic_write') as write,patch.object(m.shutil,'copy2'),patch.object(m.Path,'mkdir'),patch.object(m.subprocess,'run',side_effect=[m.subprocess.CalledProcessError(1,'systemctl'),Mock()]):
   with self.assertRaises(m.subprocess.CalledProcessError):m.apply(self.data)
   self.assertEqual(write.call_count,4)
   self.assertEqual(write.call_args_list[-2].args,(epp,epp.read_bytes.return_value))
   self.assertEqual(write.call_args_list[-1].args,(hw,hw.read_bytes.return_value))
 def test_unchanged_configuration_does_not_restart(self):
  epp,hw=Mock(),Mock()
  for p,kind in ((epp,'epp'),(hw,'hardware')):
   p.is_symlink.return_value=False;p.stat.return_value=SimpleNamespace(st_uid=0,st_mode=0o100644)
   p.read_bytes.return_value=json.dumps(self.data[kind]).encode()
  with patch.object(m,'EPP',epp),patch.object(m,'HARDWARE',hw),patch.object(m,'hardware_limits',return_value=None),patch.object(m.subprocess,'run') as run:
   self.assertFalse(m.apply(self.data)['changed']);run.assert_not_called()
if __name__=='__main__':unittest.main()
