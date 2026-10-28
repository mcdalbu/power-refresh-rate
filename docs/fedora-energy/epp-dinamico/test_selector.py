import importlib.util,json,unittest
from pathlib import Path
from unittest.mock import Mock,patch
spec=importlib.util.spec_from_file_location('selector',Path(__file__).with_name('power-profile-lpmd.py'))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ROOT=Path(__file__).parent
class Tables(unittest.TestCase):
 def setUp(self):
  self.config=json.loads((ROOT/'config.json').read_text())
  self.template=(ROOT/'template.xml').read_bytes()
 def test_all_six_contexts(self):
  for profile in m.PROFILES:
   for battery in (False,True):
    root=m.ET.fromstring(m.render(self.template,self.config,profile,battery))
    states=root.findall('./States/State')
    self.assertEqual([int(s.findtext('EPP')) for s in states],self.config[profile]['battery' if battery else 'ac'])
    self.assertEqual(root.findtext('PowersaverDef'),'0')
    self.assertEqual(root.findtext('PerformanceDef'),'0' if battery else '-1')
    self.assertEqual(root.findtext('lp_mode_epp'),'-1')
    before=m.ET.fromstring(self.template)
    for key in ('Mode','WLTHintEnable','WLTHintPollEnable','util_entry_threshold','util_exit_threshold'):
     self.assertEqual(root.findtext(key),before.findtext(key))
    for old,new in zip(before.findall('./States/State'),states):
     for element in old:
      if element.tag!='EPP': self.assertEqual(element.text,new.findtext(element.tag))
 def test_reject_invalid_epp(self):
  self.config['power-saver']['battery'][1]=256
  with self.assertRaises(ValueError):m.render(self.template,self.config,'power-saver',True)
 def test_optional_fixed_performance(self):
  self.config['performance_dynamic']['ac']=False
  root=m.ET.fromstring(m.render(self.template,self.config,'performance',False))
  self.assertEqual(root.findtext('PerformanceDef'),'-1')
 def selector(self,profile,battery):
  obj=m.Selector.__new__(m.Selector)
  obj.ppd=Mock();obj.upower=Mock()
  obj.ppd.get_cached_property.return_value.unpack.return_value=profile
  obj.upower.get_cached_property.return_value.unpack.return_value=battery
  obj.template=self.template;obj.config=self.config
  obj.pending=0;obj.last_error=None;obj.last_restart=-100;obj.key=None
  obj.GLib=Mock();obj.GLib.SOURCE_REMOVE=False
  return obj
 def test_changes_from_ac_to_battery_select_matching_table(self):
  obj=self.selector('power-saver',True)
  target=m.render(self.template,self.config,'power-saver',True)
  with patch.object(m,'XML') as xml,patch.object(m,'atomic_write') as write,patch.object(m.subprocess,'run') as run:
   xml.read_bytes.return_value=b'old'
   obj.reconcile()
   write.assert_called_once_with(xml,target)
   run.assert_called_once()
   self.assertEqual(obj.key,('power-saver',True))
 def test_unchanged_table_never_restarts(self):
  obj=self.selector('balanced',False)
  with patch.object(m,'XML') as xml,patch.object(m.subprocess,'run') as run:
   xml.read_bytes.return_value=m.render(self.template,self.config,'balanced',False)
   obj.reconcile();run.assert_not_called()
 def test_failed_restart_restores_last_xml(self):
  obj=self.selector('balanced',False)
  with patch.object(m,'XML') as xml,patch.object(m,'atomic_write') as write,patch.object(m.subprocess,'run',side_effect=[m.subprocess.CalledProcessError(1,'systemctl'),Mock()]):
   xml.read_bytes.return_value=b'old'
   obj.reconcile()
   self.assertEqual(write.call_args_list[-1].args,(xml,b'old'))
   self.assertIsNotNone(obj.last_error)
if __name__=='__main__':unittest.main()
