#!/usr/bin/python3
"""Upgrade an existing PPD/LPMD deployment with the validated preferences helper."""
from pathlib import Path
from datetime import datetime,timezone
import copy,hashlib,json,os,shutil,subprocess,tempfile,time
ROOT=Path(__file__).resolve().parent
SCRIPT=Path('/usr/local/libexec/power-profile-cpu-cap.py')
UNIT=Path('/etc/systemd/system/power-profile-cpu-cap.service')
HELPER=Path('/usr/local/libexec/power-profile-settings-helper')
POLICY=Path('/usr/share/polkit-1/actions/org.mcda.power-profile-settings.policy')
CONFIG=Path('/etc/power-profile-cpu-cap/config.json')
EPP=Path('/etc/power-profile-lpmd/config.json')

def run(*args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def install(path,content,mode):
 fd,name=tempfile.mkstemp(prefix='.power-profile-upgrade-',dir=path.parent);os.close(fd)
 temp=Path(name)
 try:
  if path.exists():shutil.copy2(path,temp)
  temp.write_bytes(content);temp.chmod(mode);os.replace(temp,path)
 finally:temp.unlink(missing_ok=True)
def main():
 assert os.geteuid()==0,'Administrator privileges required'
 assert not any(p.exists() for p in (HELPER,POLICY,CONFIG)),'Backend already installed; use the documented update procedure'
 assert EPP.exists() and SCRIPT.exists() and UNIT.exists(),'Install the PPD/LPMD deployment first'
 run('systemctl','is-active','--quiet','power-profile-lpmd.service','power-profile-cpu-cap.service')
 # This installer upgrades the already-deployed hardware-specific unit; it does
 # not substitute a different machine's GPU paths or the LPMD model XML.
 old={p:p.read_bytes() for p in (SCRIPT,UNIT)}
 original_epp=EPP.read_bytes()
 stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%SZ')
 backup=Path('/var/backups/energy-trial')/f'gnome-power-preferences-{stamp}-{os.getpid()}'
 backup.mkdir(parents=True,mode=0o700)
 for p in old:shutil.copy2(p,backup/p.name)
 shutil.copy2(EPP,backup/'epp-config.json')
 unit=old[UNIT].decode()
 assert '--settings-file' not in unit
 unit=unit.replace(' --xe-gpu-policy\n',' --xe-gpu-policy --settings-file /etc/power-profile-cpu-cap/config.json\n',1)
 assert '--settings-file' in unit
 # The initial hardware policy exactly preserves the existing 3/2 GHz caps
 # in Economy and GPU behavior; the existing EPP JSON is left untouched.
 try:
  CONFIG.parent.mkdir(mode=0o755)
  install(CONFIG,(ROOT/'hardware-defaults.json').read_bytes(),0o644)
  install(SCRIPT,(ROOT/'power-profile-cpu-cap.py').read_bytes(),0o755)
  install(UNIT,unit.encode(),0o644)
  install(HELPER,(ROOT/'power-profile-settings-helper').read_bytes(),0o755)
  install(POLICY,(ROOT/POLICY.name).read_bytes(),0o644)
  run('restorecon','-RF',str(CONFIG.parent),str(SCRIPT),str(UNIT),str(HELPER),str(POLICY))
  run('systemctl','daemon-reload')
  run('systemd-analyze','verify','--man=no',str(UNIT),'power-profile-lpmd.service','multi-user.target')
  run('systemctl','restart',UNIT.name)
  run('systemctl','is-active','--quiet',UNIT.name,'power-profile-lpmd.service','intel_lpmd.service')
  payload={'version':1,'epp':json.loads(EPP.read_text()),'hardware':json.loads(CONFIG.read_text())}
  result=run(str(HELPER),input=json.dumps(payload),text=True,capture_output=True)
  assert not json.loads(result.stdout)['changed']
  # Exercise a real apply and restore using an inactive source's idle EPP.
  profile=subprocess.check_output(['powerprofilesctl','get'],text=True).strip()
  on_battery=subprocess.check_output(['busctl','get-property','org.freedesktop.UPower','/org/freedesktop/UPower','org.freedesktop.UPower','OnBattery'],text=True).strip()=='b true'
  trial=copy.deepcopy(payload)
  inactive_source='ac' if on_battery else 'battery'
  trial['epp']['balanced'][inactive_source][0]+=1
  try:
   result=run(str(HELPER),input=json.dumps(trial),text=True,capture_output=True)
   assert json.loads(result.stdout)['changed']
  finally:
   run(str(HELPER),input=json.dumps(payload),text=True,capture_output=True)
  assert EPP.read_bytes()==original_epp
  time.sleep(2)
  assert Path('/sys/devices/system/cpu/intel_pstate/no_turbo').read_text().strip()=='0'
  result={'backup':str(backup),'profile':profile,'on_battery':on_battery,'epp_preserved':True,'helper_apply_restore':'passed','services':'active'}
  (ROOT/'installation.json').write_text(json.dumps(result,indent=2)+'\n')
  print(json.dumps(result,indent=2),flush=True)
 except BaseException:
  for p,data in old.items():install(p,data,0o755 if p==SCRIPT else 0o644)
  install(EPP,original_epp,0o644)
  for p in (HELPER,POLICY,CONFIG):p.unlink(missing_ok=True)
  if CONFIG.parent.exists():CONFIG.parent.rmdir()
  subprocess.run(['systemctl','daemon-reload'],check=False)
  subprocess.run(['systemctl','restart',UNIT.name,'power-profile-lpmd.service'],check=False)
  raise
if __name__=='__main__':main()
