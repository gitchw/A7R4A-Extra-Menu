"""Execute generated deployment scripts with fake mount/guard boundaries."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent
BUILDER=ROOT/'host-tests/build_film_runtime_package.py'
DESTS={
 'ui':'a6400-film-ui-v3-persist.so','menu_job':'a6400-film-menu-job.elf',
 'worker':'a7r4a-film-job.elf','native':'a6400-film-native-command.elf',
 'ram_job':'a6400-film-ram-job.sh','capture_daemon':'a6400-film-capture-daemon.elf',
 'guard':'a7r4a-ui-load-guard.elf','check':'a7r4a-check.req',
 'install':'a7r4a-install.req','remove':'a7r4a-remove.req'}
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

BB=r'''#!/usr/bin/env python3
import hashlib,os,sys
from pathlib import Path
b=Path(os.environ['FILM_PACKAGE_FIXTURE']);cmd=sys.argv[1];args=sys.argv[2:]
if cmd in ('mount','umount'):
 with (b/'events').open('a') as f:f.write(cmd+'\n')
 if cmd=='mount':
  (b/'proc/mounts').write_text('/dev/mmca1 '+str(b/'mnt')+' vfat ro 0 0\n')
  sys.exit(0)
 if os.environ.get('FAIL_UMOUNT'):sys.exit(1)
 if os.environ.get('LYING_UMOUNT'):sys.exit(0)
 (b/'proc/mounts').write_text('');sys.exit(0)
if cmd=='sleep':sys.exit(0)
if cmd=='sha256sum':
 p=Path(args[0]);print(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p));sys.exit(0)
os.execvp(cmd,[cmd]+args)
'''
GUARD=r'''#!/bin/sh
op=$(cat "$FILM_PACKAGE_FIXTURE/ram/a7r4a-ui-load.req")
echo "guard $op" >> "$FILM_PACKAGE_FIXTURE/events"
case "$op" in check) n=0;; install) n=1;; *) exit 3;; esac
if [ "${BAD_GUARD:-}" = "$op" ]; then echo "END result=9 op=$n errno=0"; exit 0; fi
echo "END result=0 op=$n errno=0"
if [ "${FAIL_GUARD:-}" = "$op" ]; then exit 6; fi
'''
MENU='#!/bin/sh\nprintf "%s\\n" "${MENU_LINE:-FILM_OFF}"\nexit "${MENU_RC:-10}"\n'
DAEMON='#!/bin/sh\necho "daemon $*" >> "$FILM_PACKAGE_FIXTURE/events"\n'

class FilmRuntimePackageTests(unittest.TestCase):
 def setUp(self):
  self.assertTrue(BUILDER.is_file(),'independent runtime package builder is missing')
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.base=Path(self.temp.name);self.src=self.base/'src';self.src.mkdir()
  artifacts=[]
  for role,dest in DESTS.items():
   p=self.src/dest
   data=GUARD if role=='guard' else MENU if role=='menu_job' else DAEMON if role=='capture_daemon' else role+'\n'
   p.write_text(data);p.chmod(0o700)
   artifacts.append({'role':role,'file':str(p),'dest':dest,'sha256':sha(p)})
  ui=self.src/DESTS['ui'];h=2166136261
  for n in ui.read_bytes():h=((h^n)*16777619)&0xffffffff
  pins={'path':'/tmp_bt/'+DESTS['ui'],'bytes':ui.stat().st_size,'sha256':sha(ui),
        'fnv':f'{h:08x}','ui_rva':'0x14070','ui_bytes':1120,'lifetime_ms':0,
        'build_defines':{'FILM_SETTINGS_PERSISTENT':1}}
  self.pins=self.base/'pinned-ui.json';self.pins.write_text(json.dumps(pins))
  capture_pins={'persistent':True,'active_ms':600000,'job_timeout_ms':600000,
                'runtime':'/tmp_bt/'+DESTS['capture_daemon'],'arguments':['watch'],
                'daemon_sha256':sha(self.src/DESTS['capture_daemon']),
                'daemon_bytes':(self.src/DESTS['capture_daemon']).stat().st_size}
  for key,role in [('script','ram_job'),('settings','menu_job')]:
   p=self.src/DESTS[role];h=2166136261
   for n in p.read_bytes():h=((h^n)*16777619)&0xffffffff
   capture_pins[key]={'runtime':'/tmp_bt/'+DESTS[role],'bytes':p.stat().st_size,
                      'fnv':f'{h:08x}','sha256':sha(p)}
  self.capture_pins=self.base/'pinned-inputs.json';self.capture_pins.write_text(json.dumps(capture_pins))
  self.config={'schema':1,'artifacts':artifacts,'menu_pins_file':str(self.pins),
               'capture_pins_file':str(self.capture_pins),
               'default_enabled':False,'capture_persistent':True}
  self.spec=self.base/'artifacts.json';self.out=self.base/'package'

 def build(self):
  self.spec.write_text(json.dumps(self.config))
  return subprocess.run([sys.executable,str(BUILDER),str(self.spec),str(self.out)],
                        capture_output=True,text=True,timeout=10)

 def stage_fixture(self):
  result=self.build();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
  self.mnt=self.base/'mnt';shutil.copytree(self.out/'A6400',self.mnt/'A6400')
  self.ram=self.base/'ram';self.ram.mkdir()
  proc=self.base/'proc';(proc/'149/fd').mkdir(parents=True)
  (proc/'mounts').write_text('');(proc/'uptime').write_text('2.00 1.00\n')
  (proc/'149/comm').write_text('im.elf\n')
  (proc/'149/maps').write_text('00008000-0000d000 r-xp 0 0:0 1 /usr/bin/im.elf\n'
      '01000000-02000000 r-xp 0 0:0 2 /usr/lib/libObj.so\n'
      '03000000-04000000 r-xp 0 0:0 3 /usr/lib/viewUnified2.so\n')
  (self.ram/'ae.lock').touch()
  self.bb=self.base/'bb';self.bb.write_text(BB);self.bb.chmod(0o700)
  runtime=self.mnt/'A6400/RUNTIME/runtime-start.sh'
  prepare=self.mnt/'A6400/RUNTIME/prepare.sh'
  bootstrap=self.base/'bootstrap-v2.sh'
  old_runtime_sha=sha(runtime)
  def adjusted(text):
   return (text.replace('/bin/busybox',str(self.bb))
           .replace('/tmp_bt',str(self.ram)).replace('/mnt',str(self.mnt))
           .replace('/proc',str(proc)).replace('/tmp/disable_hook',str(self.base/'disable_hook')))
  runtime.write_text(adjusted(runtime.read_text()))
  old_prepare_sha=sha(prepare)
  prepare.write_text(adjusted(prepare.read_text()).replace(old_runtime_sha,sha(runtime)))
  bootstrap.write_text(adjusted((self.out/'bootstrap-v2.sh').read_text())
                       .replace(old_prepare_sha,sha(prepare)).replace(old_runtime_sha,sha(runtime)))
  self.prepare=prepare;self.bootstrap=bootstrap

 def run_script(self,path,**env):
  return subprocess.run(['sh',str(path)],capture_output=True,text=True,
     # Python command doubles can take longer on an ARM development board.
     # This host deadline does not change camera readiness or I/O limits.
     env=dict(os.environ,FILM_PACKAGE_FIXTURE=str(self.base),**env),timeout=30)

 def test_build_pins_all_artifacts_and_matching_persistent_module(self):
  r=self.build();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  manifest=json.loads((self.out/'manifest.json').read_text())
  self.assertEqual(manifest['module_pins']['lifetime_ms'],0)
  self.assertFalse(manifest['default_enabled'])
  for row in manifest['files']:
   self.assertEqual(sha(self.out/row['path']),row['sha256'])
  self.assertTrue((self.out/'A6400/RUNTIME/prepare.sh').is_file())

 def test_missing_required_artifact_refuses_package(self):
  self.config['artifacts']=[x for x in self.config['artifacts'] if x['role']!='capture_daemon']
  r=self.build();self.assertNotEqual(r.returncode,0)
  self.assertFalse(self.out.exists())

 def test_mismatched_or_finite_menu_pin_refuses_package(self):
  pins=json.loads(self.pins.read_text());pins['lifetime_ms']=600000
  self.pins.write_text(json.dumps(pins))
  r=self.build();self.assertNotEqual(r.returncode,0)
  self.assertFalse(self.out.exists())

 def test_wrong_artifact_pin_refuses_package(self):
  self.config['artifacts'][0]['sha256']='0'*64
  r=self.build();self.assertNotEqual(r.returncode,0)
  self.assertFalse(self.out.exists())

 def test_reader_pin_must_match_the_exact_persistent_module(self):
  pins=json.loads(self.pins.read_text());pins['fnv']='00000000'
  self.pins.write_text(json.dumps(pins))
  r=self.build();self.assertNotEqual(r.returncode,0)
  self.assertFalse(self.out.exists())

 def test_finite_daemon_build_is_rejected_despite_persistent_config(self):
  pins=json.loads(self.capture_pins.read_text());pins['persistent']=False
  self.capture_pins.write_text(json.dumps(pins))
  r=self.build();self.assertNotEqual(r.returncode,0)
  self.assertFalse(self.out.exists())

 def test_daemon_must_pin_the_exact_final_job_script_and_settings(self):
  pins=json.loads(self.capture_pins.read_text());pins['script']['sha256']='0'*64
  self.capture_pins.write_text(json.dumps(pins))
  r=self.build();self.assertNotEqual(r.returncode,0)
  self.assertFalse(self.out.exists())

 def test_daemon_receipt_requires_matching_output_elf_identity(self):
  pins=json.loads(self.capture_pins.read_text());del pins['daemon_sha256']
  self.capture_pins.write_text(json.dumps(pins))
  r=self.build();self.assertNotEqual(r.returncode,0)
  self.assertFalse(self.out.exists())

 def test_prepare_copies_and_verifies_without_executing_any_artifact(self):
  self.stage_fixture();r=self.run_script(self.prepare)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  for row in self.config['artifacts']:self.assertEqual(sha(self.ram/row['dest']),row['sha256'])
  self.assertFalse((self.base/'events').exists())
  self.assertTrue((self.ram/'a6400-film-staged').is_file())

 def test_prepare_corruption_is_detected_before_any_artifact_copy(self):
  self.stage_fixture();(self.mnt/'A6400/RUNTIME'/DESTS['native']).write_text('corrupt')
  r=self.run_script(self.prepare);self.assertNotEqual(r.returncode,0)
  for dest in DESTS.values():self.assertFalse((self.ram/dest).exists())
  self.assertFalse((self.base/'events').exists())

 def test_prepare_preserves_an_existing_ram_artifact(self):
  self.stage_fixture();existing=self.ram/DESTS['worker'];existing.write_bytes(b'owned elsewhere')
  r=self.run_script(self.prepare);self.assertNotEqual(r.returncode,0)
  self.assertEqual(existing.read_bytes(),b'owned elsewhere')
  self.assertFalse((self.ram/DESTS['ui']).exists())

 def test_bootstrap_unmount_failure_never_starts_guard_or_daemon(self):
  self.stage_fixture();r=self.run_script(self.bootstrap,FAIL_UMOUNT='1')
  self.assertNotEqual(r.returncode,0)
  events=(self.base/'events').read_text().splitlines()
  self.assertEqual(events[0],'mount')
  self.assertNotIn('guard check',events);self.assertNotIn('daemon watch',events)

 def test_bootstrap_unmounts_before_check_install_and_daemon(self):
  self.stage_fixture();r=self.run_script(self.bootstrap)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertEqual((self.base/'events').read_text().splitlines(),
                   ['mount','umount','guard check','guard install','daemon watch'])

 def test_bootstrap_requires_mount_table_to_confirm_unmount(self):
  self.stage_fixture();r=self.run_script(self.bootstrap,LYING_UMOUNT='1')
  self.assertNotEqual(r.returncode,0)
  self.assertEqual((self.base/'events').read_text().splitlines(),['mount','umount'])

 def test_runtime_requires_guard_end_zero_even_when_process_exits_zero(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh',BAD_GUARD='check')
  self.assertNotEqual(r.returncode,0)
  self.assertEqual((self.base/'events').read_text().splitlines(),['guard check'])

 def test_runtime_requires_zero_guard_exit_even_when_end_says_zero(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh',FAIL_GUARD='check')
  self.assertNotEqual(r.returncode,0)
  self.assertEqual((self.base/'events').read_text().splitlines(),['guard check'])

 def test_runtime_install_failure_never_starts_daemon(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh',BAD_GUARD='install')
  self.assertNotEqual(r.returncode,0)
  self.assertEqual((self.base/'events').read_text().splitlines(),['guard check','guard install'])

 def test_runtime_corrupt_ram_artifact_prevents_any_guard_action(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  (self.ram/DESTS['ui']).write_bytes(b'changed after staging')
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh')
  self.assertNotEqual(r.returncode,0)
  self.assertFalse((self.base/'events').exists())

 def test_runtime_readiness_wait_is_bounded_with_wrong_process_identity(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  (self.base/'proc/149/comm').write_text('other-process\n')
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh')
  self.assertNotEqual(r.returncode,0)
  self.assertIn('im_not_ready',r.stdout+r.stderr)
  self.assertFalse((self.base/'events').exists())

 def test_runtime_stops_before_guard_when_a_native_operation_is_unresolved(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  (self.ram/'a6400-film-capture.inflight').touch()
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh')
  self.assertNotEqual(r.returncode,0)
  self.assertFalse((self.base/'events').exists())

 def test_runtime_preserves_existing_runtime_log_before_any_guard_action(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  log=self.ram/'a6400-film-capture.runtime.log';log.write_bytes(b'retained diagnostic evidence\n')
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh')
  self.assertNotEqual(r.returncode,0)
  self.assertIn('FILM_RUNTIME_FAIL stage=existing_runtime_log',r.stdout)
  self.assertNotIn('FILM_RUNTIME_READY',r.stdout)
  self.assertEqual(log.read_bytes(),b'retained diagnostic evidence\n')
  self.assertFalse((self.base/'events').exists())
  self.assertFalse((self.ram/'a6400-film-runtime.lock').exists())
  self.assertFalse((self.ram/'a7r4a-ui-load.req').exists())

 def test_runtime_preserves_runtime_log_symlink_before_any_guard_action(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  target=self.base/'retained.log';target.write_bytes(b'original target evidence\n')
  log=self.ram/'a6400-film-capture.runtime.log';log.symlink_to(target)
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh')
  self.assertNotEqual(r.returncode,0)
  self.assertIn('FILM_RUNTIME_FAIL stage=existing_runtime_log',r.stdout)
  self.assertNotIn('FILM_RUNTIME_READY',r.stdout)
  self.assertTrue(log.is_symlink())
  self.assertEqual(log.readlink(),target)
  self.assertEqual(target.read_bytes(),b'original target evidence\n')
  self.assertFalse((self.base/'events').exists())
  self.assertFalse((self.ram/'a6400-film-runtime.lock').exists())
  self.assertFalse((self.ram/'a7r4a-ui-load.req').exists())

 def test_runtime_preserves_dangling_runtime_log_symlink_before_any_guard_action(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  target=self.base/'absent.log'
  log=self.ram/'a6400-film-capture.runtime.log';log.symlink_to(target)
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh')
  self.assertNotEqual(r.returncode,0)
  self.assertIn('FILM_RUNTIME_FAIL stage=existing_runtime_log',r.stdout)
  self.assertNotIn('FILM_RUNTIME_READY',r.stdout)
  self.assertTrue(log.is_symlink())
  self.assertEqual(log.readlink(),target)
  self.assertFalse(target.exists())
  self.assertFalse((self.base/'events').exists())
  self.assertFalse((self.ram/'a6400-film-runtime.lock').exists())
  self.assertFalse((self.ram/'a7r4a-ui-load.req').exists())

 def test_runtime_stop_marker_is_preserved_and_prevents_startup(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  stop=self.ram/'a6400-film-capture.stop';stop.touch()
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh')
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertTrue(stop.is_file())
  self.assertFalse((self.base/'events').exists())

 def test_runtime_unloaded_ui_is_unavailable_and_can_start_gated_daemon(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh',MENU_RC='11',MENU_LINE='FILM_UNAVAILABLE source=1')
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertEqual((self.base/'events').read_text().splitlines(),['guard check','guard install','daemon watch'])

 def test_runtime_rejects_initial_on_without_starting_daemon(self):
  self.stage_fixture();self.assertEqual(self.run_script(self.prepare).returncode,0)
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh',MENU_RC='0',
                    MENU_LINE='FILM_JOB preset=kodak_gold grain=2 halation=1')
  self.assertNotEqual(r.returncode,0)
  self.assertNotIn('daemon watch',(self.base/'events').read_text())

 def test_late_boot_or_camera_owned_sd_never_mounts_raw_sd(self):
  self.stage_fixture();(self.base/'proc/uptime').write_text('90.00 2.00\n')
  r=self.run_script(self.bootstrap);self.assertNotEqual(r.returncode,0)
  self.assertFalse((self.base/'events').exists())

 def test_camera_native_sd_handle_prevents_raw_sd_mount_even_early(self):
  self.stage_fixture();(self.base/'proc/149/fd/68').symlink_to('/dev/mmca')
  r=self.run_script(self.bootstrap);self.assertNotEqual(r.returncode,0)
  self.assertFalse((self.base/'events').exists())

 def test_bootstrap_records_native_sd_owner_before_refusing_mount(self):
  self.stage_fixture();(self.base/'proc/149/fd/68').symlink_to('/dev/mmca')
  r=self.run_script(self.bootstrap);self.assertNotEqual(r.returncode,0)
  log=self.ram/'a6400-film-boot.log'
  self.assertTrue(log.is_file(),'early boot refusal must leave diagnostic evidence')
  text=log.read_text()
  self.assertIn('FILM_BOOT_BEGIN',text)
  self.assertIn('reason=camera_sd_handle',text)
  self.assertIn('target=/dev/mmca',text)
  self.assertIn('FILM_BOOT_FAIL stage=not_early_exclusive_boot',text)
  self.assertFalse((self.base/'events').exists())

 def test_bootstrap_records_late_boot_uptime_before_refusing_mount(self):
  self.stage_fixture();(self.base/'proc/uptime').write_text('90.25 2.00\n')
  r=self.run_script(self.bootstrap);self.assertNotEqual(r.returncode,0)
  log=self.ram/'a6400-film-boot.log'
  self.assertTrue(log.is_file(),'late boot refusal must leave diagnostic evidence')
  self.assertIn('reason=late_boot uptime=90.25',log.read_text())
  self.assertFalse((self.base/'events').exists())

 def test_bootstrap_preserves_existing_diagnostic_log_without_mounting(self):
  self.stage_fixture();log=self.ram/'a6400-film-boot.log'
  log.write_bytes(b'prior boot diagnostic evidence\n')
  r=self.run_script(self.bootstrap);self.assertNotEqual(r.returncode,0)
  self.assertEqual(log.read_bytes(),b'prior boot diagnostic evidence\n')
  self.assertFalse((self.base/'events').exists())

 def test_bootstrap_log_keeps_runtime_startup_result(self):
  self.stage_fixture();r=self.run_script(self.bootstrap)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  log=self.ram/'a6400-film-boot.log'
  self.assertTrue(log.is_file(),'cold runtime must retain startup output')
  text=log.read_text()
  self.assertIn('FILM_BOOT_STAGED_AND_UNMOUNTED',text)
  self.assertIn('FILM_RUNTIME_READY initial_enabled=0',text)

 def test_card_disable_gate_unmounts_without_staging(self):
  self.stage_fixture();(self.mnt/'A6400/DISABLE').touch()
  r=self.run_script(self.bootstrap);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertEqual((self.base/'events').read_text().splitlines(),['mount','umount'])
  self.assertFalse((self.ram/DESTS['ui']).exists())

 def test_disable_gate_prevents_mount_and_runtime(self):
  self.stage_fixture();(self.base/'disable_hook').touch()
  r=self.run_script(self.bootstrap);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertFalse((self.base/'events').exists())

 def test_slot2_mmcb_mount_and_dynamic_pid_162_are_supported(self):
  self.stage_fixture()
  shutil.move(self.base/'proc/149',self.base/'proc/162')
  r=self.run_script(self.bootstrap)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertEqual((self.base/'events').read_text().splitlines(),
                   ['mount','umount','guard check','guard install','daemon watch'])

  # Slot 2 (/dev/mmcb1) mounted in /proc/mounts must block runtime and bootstrap
  (self.base/'proc/mounts').write_text('/dev/mmcb1 /mnt/sd2 vfat rw 0 0\n')
  r=self.run_script(self.ram/'a6400-film-runtime-start.sh')
  self.assertNotEqual(r.returncode,0)
  self.assertIn('linux_sd_mounted',r.stdout)

  # Slot 2 fd (/dev/mmcb1) under PID 162 must block early_boot
  (self.base/'proc/mounts').write_text('')
  (self.ram/'a6400-film-boot.log').unlink()
  (self.base/'proc/162/fd/68').symlink_to('/dev/mmcb1')
  r=self.run_script(self.bootstrap)
  self.assertNotEqual(r.returncode,0)
  self.assertIn('target=/dev/mmcb1',(self.ram/'a6400-film-boot.log').read_text())

 def test_bootstrap_mounts_tmpfs_when_ram_dir_is_read_only(self):
  self.stage_fixture()
  self.bb.write_text('''#!/usr/bin/env python3
import hashlib,os,sys
from pathlib import Path
b=Path(os.environ['FILM_PACKAGE_FIXTURE']);cmd=sys.argv[1];args=sys.argv[2:]
if cmd=='mount' and 'tmpfs' in args:
 os.chmod(b/'ram',0o755)
 with (b/'events').open('a') as f:f.write('mount tmpfs\\n')
 sys.exit(0)
if cmd in ('mount','umount'):
 with (b/'events').open('a') as f:f.write(cmd+'\\n')
 if cmd=='mount':
  (b/'proc/mounts').write_text('/dev/mmca1 '+str(b/'mnt')+' vfat ro 0 0\\n')
  sys.exit(0)
 (b/'proc/mounts').write_text('');sys.exit(0)
if cmd=='sleep':sys.exit(0)
if cmd=='sha256sum':
 p=Path(args[0]);print(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p));sys.exit(0)
os.execvp(cmd,[cmd]+args)
''')
  self.bb.chmod(0o700)
  self.ram.chmod(0o555)
  self.addCleanup(lambda: self.ram.chmod(0o755) if self.ram.exists() else None)
  r=self.run_script(self.bootstrap)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertEqual((self.base/'events').read_text().splitlines(),
                   ['mount tmpfs','mount','umount','guard check','guard install','daemon watch'])

 def test_stale_ram_job_settings_sha_is_rejected(self):
  ram=self.src/DESTS['ram_job']
  ram.write_text('#!/bin/sh\nNATIVE_SHA='+sha(self.src/DESTS['native'])+'\nWORKER_SHA='+sha(self.src/DESTS['worker'])+'\nSETTINGS_SHA='+'0'*64+'\n')
  for row in self.config['artifacts']:
   if row['role']=='ram_job':row['sha256']=sha(ram)
  cp=json.loads(self.capture_pins.read_text())
  data=ram.read_bytes();h=2166136261
  for n in data:h=((h^n)*16777619)&0xffffffff
  cp['script']={'runtime':'/tmp_bt/'+DESTS['ram_job'],'bytes':len(data),'fnv':f'{h:08x}','sha256':sha(ram)}
  self.capture_pins.write_text(json.dumps(cp))
  r=self.build()
  self.assertNotEqual(r.returncode,0)
  self.assertIn('RAM job SETTINGS_SHA mismatch',r.stderr+r.stdout)

if __name__=='__main__':unittest.main(verbosity=2)
