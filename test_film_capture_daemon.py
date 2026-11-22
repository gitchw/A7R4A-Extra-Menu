"""Run the real daemon; replace only the unavailable Sony monitor and job tools.

The job double records dispatch and native-client overlap. It does not pretend
to validate a JPEG, perform a native SD write, or prove shooting-mode behavior.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import unittest

from synthetic_native_frames import (
    SYNTHETIC_FNEXT_UNMOUNTED,
    SYNTHETIC_INTERLEAVED_FNEXT_METADATA,
    SYNTHETIC_ROOT_VERSION,
)
from test_native_capture_probe import MONITOR as SCANNER_MONITOR, fnv

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'camera/film_capture_daemon.c'
ON = 'FILM_JOB preset=kodak_gold grain=2 halation=1\n'
MONITOR = SCANNER_MONITOR.replace(
    "for command in sys.stdin:",
    "for command in sys.stdin:\n with (root/'scan-commands').open('a') as f:f.write(command)")
SETTINGS = r'''#!/usr/bin/env python3
import os,signal,sys,time
from pathlib import Path
root=Path(os.environ['CAPTURE_FIXTURE'])
(root/'settings-started').write_text('started')
time.sleep(float(os.environ.get('FILM_CAPTURE_SETTINGS_DELAY','0')))
text=(root/'settings').read_text()
(root/'settings-observed').write_text(text)
if os.environ.get('FILM_CAPTURE_SETTINGS_FAULT')=='signal':os.kill(os.getpid(),signal.SIGTERM)
if os.environ.get('FILM_CAPTURE_SETTINGS_FAULT')=='exit127':sys.exit(127)
print(text,end='',flush=True)
sys.exit(10 if text=='FILM_OFF\n' else 11 if text.startswith('FILM_UNAVAILABLE') else 0)
'''
JOB = r'''import json,os,sys,time
from pathlib import Path
root=Path(os.environ['CAPTURE_FIXTURE'])
fault=os.environ.get('FILM_CAPTURE_JOB_FAULT','')
pid=int((root/'monitor.pid').read_text())
try:os.kill(pid,0)
except ProcessLookupError:pass
else:
 print('RAM_JOB_RETAINED phase=monitor_overlap',flush=True);sys.exit(21)
args=sys.argv[1:]
if len(args)!=4 or args[0]!='apply':sys.exit(2)
mode,source,size,target=args
expected=root/source.removeprefix('A:/').removeprefix('B:/')
if not expected.is_file() or expected.stat().st_size!=int(size):sys.exit(3)
settings=(root/'settings').read_text().strip()
record=dict(source=source,size=int(size),target=target,settings=settings)
with (root/'jobs').open('a') as f:f.write(json.dumps(record)+'\n')
(root/'job-started').write_text('started')
(root/'job.lock').mkdir()
time.sleep(float(os.environ.get('FILM_CAPTURE_JOB_DELAY','.12')))
if fault=='uncertain':
 print('RAM_JOB_RETAINED phase=native_read result=21',flush=True);sys.exit(21)
if fault=='retained':
 print('RAM_JOB_RETAINED phase=cleanup result=1',flush=True);sys.exit(1)
(root/'job.lock').rmdir()
if fault=='failed_first' and len((root/'jobs').read_text().splitlines())==1:
 print('RAM_JOB_FAIL stage=settings_changed',flush=True);sys.exit(12)
if fault=='failed_flood':
 print('x'*4096+'\x1b[31m\nRAM_JOB_FAIL stage=settings_changed',flush=True);sys.exit(12)
if fault=='missing_receipt':sys.exit(0)
if fault=='failed':
 print('RAM_JOB_FAIL stage=off',flush=True);sys.exit(10)
parts=settings.split()
verified='0' if fault=='unverified_target' else '1'
print('RAM_JOB_APPLIED source='+source+' target='+target+' '+' '.join(parts[1:])+
      ' source_bytes='+size+' source_sha256='+'a'*64+
      ' output_bytes=123 output_sha256='+'b'*64+
      ' source_access=read_only target_verified='+verified+' original_verified=0',flush=True)
'''


class FilmCaptureDaemonTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCE.is_file(), 'automatic menu-controlled capture daemon is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.dcim = self.base / 'DCIM/10061115'
        self.dcim.mkdir(parents=True)
        (self.dcim / 'A6400001.JPG').write_bytes(b'old JPEG stand-in')
        (self.base / 'settings').write_text(ON)
        self.monitor = self.base / 'monitor'
        self.monitor.write_text(MONITOR)
        self.monitor.chmod(0o700)
        self.settings = self.base / 'settings-cli'
        self.settings.write_text(SETTINGS)
        self.settings.chmod(0o700)
        self.helper = self.base / 'job.py'
        self.helper.write_text(JOB)
        self.script = self.base / 'ram-job.sh'
        self.script.write_text(f'#!/bin/sh\nexec python3 "{self.helper}" "$@"\n')
        self.build_daemon()

    def build_daemon(self, extra_probe=(), extra_build=()):
        (self.base / 'capture_probe_build.h').write_text(
            f'#define CAPTURE_MONITOR "{self.monitor}"\n'
            f'#define CAPTURE_LOCK "{self.base}/capture.lock"\n'
            f'#define CAPTURE_STOP_FILE "{self.base}/stop"\n'
            f'#define CAPTURE_MONITOR_BYTES {self.monitor.stat().st_size}u\n'
            f'#define CAPTURE_MONITOR_FNV 0x{fnv(self.monitor.read_bytes()):08x}u\n'
            '#define CAPTURE_STARTUP_MS 100u\n#define CAPTURE_COMMAND_MS 1000u\n'
            '#define CAPTURE_PROMPT_QUIET_MS 20u\n'
            '#define CAPTURE_WAIT_MS 10u\n#define CAPTURE_ACTIVE_MS 2200u\n'
            '#define CAPTURE_INTERVAL_MS 20u\n#define CAPTURE_STABLE_MS 80u\n'
            + ''.join(f'{line}\n' for line in extra_probe))
        header = []
        for macro, path in [('SETTINGS', self.settings), ('SCRIPT', self.script)]:
            header += [f'#define FILM_CAPTURE_{macro} "{path}"\n',
                       f'#define FILM_CAPTURE_{macro}_BYTES {path.stat().st_size}u\n',
                       f'#define FILM_CAPTURE_{macro}_FNV 0x{fnv(path.read_bytes()):08x}u\n']
        for macro, name in [('MANIFEST', 'manifest'), ('INFLIGHT', 'inflight'),
                            ('JOB_LOG', 'job.log'), ('JOB_LOCK', 'job.lock'),
                            ('RUNTIME_LOG', 'runtime.log')]:
            header += [f'#define FILM_CAPTURE_{macro} "{self.base/name}"\n']
        header += ['#define FILM_CAPTURE_ACTIVE_MS 2200u\n',
                   '#define FILM_CAPTURE_PERSISTENT 0\n',
                   '#define FILM_CAPTURE_BASELINE_TIMEOUT_MS 2000u\n',
                   '#define FILM_CAPTURE_CLI_TIMEOUT_MS 1000u\n',
                   '#define FILM_CAPTURE_JOB_TIMEOUT_MS 2500u\n']
        header += [f'{line}\n' for line in extra_build]
        (self.base / 'film_capture_daemon_build.h').write_text(''.join(header))
        self.binary = self.base / 'daemon'
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', str(self.base), str(SOURCE),
                        str(ROOT/'camera/film_runtime_log.c'), '-o', str(self.binary)],
                       check=True, capture_output=True)

    def jobs(self):
        path = self.base / 'jobs'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def new(self, number=2):
        (self.dcim / f'A640{number:04d}.JPG').write_bytes(b'new photo ' + str(number).encode())

    def after_record(self, name, fragment, action):
        """Synchronize a human action to a real external receipt, not a delay."""
        def event(process):
            deadline = time.monotonic() + 4
            while process.poll() is None and time.monotonic() < deadline:
                path = self.base / name
                if path.exists() and fragment in path.read_text():
                    action()
                    return
                time.sleep(.005)
            raise AssertionError(f'expected fixture receipt was not observed: {name}: {fragment}')
        return event

    def run_daemon(self, events=(), settings=ON, fault='', monitor_fault='', job_delay='.12', settings_delay='0', ready_events=(), settings_fault=''):
        (self.base / 'settings').write_text(settings)
        # Each call below is a separate test launch; prior evidence was consumed.
        (self.base / 'runtime.log').unlink(missing_ok=True)
        env = dict(os.environ, CAPTURE_FIXTURE=str(self.base), CAPTURE_FAULT=monitor_fault,
                   FILM_CAPTURE_JOB_FAULT=fault, FILM_CAPTURE_JOB_DELAY=job_delay,
                   FILM_CAPTURE_SETTINGS_DELAY=settings_delay,
                   FILM_CAPTURE_SETTINGS_FAULT=settings_fault)
        process = subprocess.Popen([str(self.binary), 'watch'], env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lines = []
        baseline = threading.Event()
        ready = threading.Event()
        cancel = threading.Event()
        event_errors = []

        def consume():
            # These short existing cases do not roll64KiB. Runtime-only cap
            # behavior has separate flood tests against the actual logger.
            offset=0
            while True:
                path=self.base/'runtime.log'
                if path.exists():
                    data=path.read_text()
                    for line in data[offset:].splitlines(keepends=True):
                        lines.append(line)
                        if line.startswith('FILM_CAPTURE_BASELINE '): baseline.set()
                        if line.startswith('FILM_CAPTURE_READY '): ready.set()
                    offset=len(data)
                if process.poll() is not None: break
                time.sleep(.003)

        reader = threading.Thread(target=consume)
        reader.start()
        threads = []
        for trigger, group in ((baseline, events), (ready, ready_events)):
            for delay, action in group:
                def event(delay=delay, action=action, trigger=trigger):
                    if trigger.wait(3) and not cancel.wait(delay):
                        try:
                            action(process)
                        except Exception as error:
                            event_errors.append(repr(error))
                thread = threading.Thread(target=event)
                thread.start()
                threads.append(thread)
        try:
            process.wait(timeout=8)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            cancel.set()
            for thread in threads:
                thread.join()
            reader.join()
            error = process.stderr.read()
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(event_errors, [], 'background fixture action failed: ' + ''.join(lines))
        return process.returncode, ''.join(lines) + error

    def test_baseline_is_never_processed(self):
        rc, out = self.run_daemon()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.jobs(), [])
        self.assertIn('FILM_CAPTURE_BASELINE count=1', out)
        self.assertFalse((self.base / 'capture.lock').exists())

    def corrupt_metadata(self, mode='once', target='A6400003.JPG'):
        (self.base/'metadata-reply').write_bytes(SYNTHETIC_INTERLEAVED_FNEXT_METADATA)
        (self.base/'metadata-fault-target').write_text(target)
        (self.base/'metadata-fault-mode').write_text(mode)

    def assert_fresh_root_after_corruption(self, out, count=1):
        commands=(self.base/'scan-commands').read_text().splitlines()
        faults=[i for i,line in enumerate(commands) if line=='METADATA_FAULT']
        self.assertEqual(len(faults),count,out)
        for i in faults[:2]:
            self.assertEqual(commands[i+1],'MPR FileIf FFirst ms:/DCIM/*',out)

    def test_corrupt_baseline_is_discarded_then_existing_photos_stay_old(self):
        self.new(3)
        self.corrupt_metadata()
        rc,out=self.run_daemon()
        self.assertEqual(rc,0,out)
        self.assertIn('FILM_CAPTURE_BASELINE count=2',out)
        self.assertEqual(self.jobs(),[],out)
        self.assert_fresh_root_after_corruption(out)

    def test_nul_interleaved_root_frame_is_retried_from_fresh_root(self):
        rc,out=self.run_daemon(monitor_fault='nul_metadata')
        self.assertEqual(rc,0,out)
        self.assertIn('FILM_CAPTURE_BASELINE count=1',out)
        self.assertIn('FILM_CAPTURE_SCAN_RETRY stage=baseline',out)
        self.assertIn('FILM_CAPTURE_SCAN_RESUMED stage=baseline',out)
        self.assertEqual(self.jobs(),[],out)

    def test_persistent_nul_interleaving_stops_after_three_fresh_roots(self):
        (self.base/'nul-metadata-always').write_text('1')
        rc,out=self.run_daemon(monitor_fault='nul_metadata')
        self.assertEqual(rc,21,out)
        self.assertNotIn('FILM_CAPTURE_BASELINE count=',out)
        self.assertEqual(out.count('FILM_CAPTURE_SCAN_RETRY stage=baseline'),2,out)
        self.assertIn('FILM_CAPTURE_SCAN_EXHAUSTED stage=baseline',out)
        self.assertIn('uncertain=1\n',(self.base/'manifest').read_text())
        self.assertEqual(self.jobs(),[],out)

    def test_corrupt_watch_discards_partial_snapshot_and_dispatches_new_photo_once(self):
        self.new(3)
        def interrupt(_):
            self.corrupt_metadata()
            self.new(2)
        rc,out=self.run_daemon(events=[(.04,interrupt)])
        self.assertEqual(rc,0,out)
        self.assertEqual([job['source'] for job in self.jobs()],
                         ['A:/DCIM/10061115/A6400002.JPG'],out)
        self.assertNotIn('A6409682.JPG',str(self.jobs()))
        self.assert_fresh_root_after_corruption(out)

    def test_persistent_corruption_stops_after_three_inventories_without_dispatch(self):
        self.new(3)
        def interrupt(_):
            self.corrupt_metadata('always')
            self.new(2)
        rc,out=self.run_daemon(events=[(.04,interrupt)])
        self.assertEqual(rc,21,out)
        self.assertEqual((self.base/'metadata-fault-count').read_text(),'3',out)
        self.assertEqual(self.jobs(),[],out)
        self.assertTrue((self.base/'capture.lock').exists())
        self.assertIn('uncertain=1\n',(self.base/'manifest').read_text())
        self.assert_fresh_root_after_corruption(out,3)
        self.assertTrue((self.base/'scan-commands').read_text().endswith('METADATA_FAULT\n'))

    def test_corrupt_baseline_retry_limit_never_publishes_partial_baseline(self):
        self.new(3)
        self.corrupt_metadata('always')
        rc,out=self.run_daemon()
        self.assertEqual(rc,21,out)
        self.assertEqual((self.base/'metadata-fault-count').read_text(),'3',out)
        self.assertNotIn('FILM_CAPTURE_BASELINE count=',out)
        self.assertEqual(self.jobs(),[],out)

    def assert_fatal_after_corruption(self, fault, reason):
        self.new(3)
        self.corrupt_metadata()
        (self.base/'metadata-next-fault').write_text(fault)
        rc,out=self.run_daemon()
        self.assertEqual(rc,21,out)
        self.assertEqual((self.base/'metadata-fault-count').read_text(),'1',out)
        self.assertIn('CAPTURE_DIAG reason='+reason+' ',out)
        self.assertEqual(self.jobs(),[],out)
        self.assertEqual(len((self.base/'scan-commands').read_text().splitlines()),6,out)

    def test_native_error_after_corruption_stops_without_retrying_error(self):
        self.assert_fatal_after_corruption('native_error','native_status')

    def test_overflow_after_corruption_stops_without_retrying_transport(self):
        self.assert_fatal_after_corruption('overflow','ring_overflow')

    def test_unknown_callback_after_corruption_stops_without_retrying_callback(self):
        self.assert_fatal_after_corruption('unknown_callback','invalid_callback')

    def test_timeout_after_corruption_stops_without_retrying_incomplete_command(self):
        self.assert_fatal_after_corruption('no_prompt','command_timeout')

    def test_successful_baseline_resets_limit_for_later_watch_corruption(self):
        self.new(3)
        self.corrupt_metadata()
        rc,out=self.run_daemon(events=[(.04,lambda _:
            (self.base/'metadata-fault-mode').write_text('always'))])
        self.assertEqual(rc,21,out)
        self.assertIn('FILM_CAPTURE_BASELINE count=2',out)
        self.assertEqual((self.base/'metadata-fault-count').read_text(),'4',out)
        self.assertEqual(self.jobs(),[],out)

    def test_recovery_does_not_reprocess_an_already_applied_photo(self):
        self.new(3)
        def interrupt():
            self.corrupt_metadata()
            self.new(4)
        rc,out=self.run_daemon(events=[(.04,lambda _:self.new()),
            (.04,self.after_record('runtime.log','FILM_CAPTURE_JOB_DONE result=0 ',interrupt))])
        self.assertEqual(rc,0,out)
        self.assertEqual([job['source'] for job in self.jobs()],
                         ['A:/DCIM/10061115/A6400002.JPG','A:/DCIM/10061115/A6400004.JPG'],out)
        self.assert_fresh_root_after_corruption(out)

    def test_corruption_resets_candidate_stability_before_any_dispatch(self):
        self.new(3)
        def interrupt():
            self.corrupt_metadata()
            (self.base/'metadata-fault-delay').write_text('.15')
        seen='state=waiting source=A:/DCIM/10061115/A6400002.JPG'
        rc,out=self.run_daemon(events=[(.04,lambda _:self.new()),
            (.04,self.after_record('manifest',seen,interrupt))])
        self.assertEqual(rc,0,out)
        self.assertEqual(len(self.jobs()),1,out)
        lines=out.splitlines()
        recovered=next(line for line in lines if line.startswith('FILM_CAPTURE_SCAN_RESUMED '))
        pending=next(line for line in lines if line.startswith('FILM_CAPTURE_DISPATCH '))
        at=lambda line:int(line.split('uptime_ms=')[1].split()[0])
        self.assertGreaterEqual(at(pending)-at(recovered),80,out)

    def test_stop_during_corruption_retry_starts_no_more_native_command(self):
        self.new(3)
        self.corrupt_metadata()
        rc,out=self.run_daemon(ready_events=[(.01,self.after_record(
            'runtime.log','FILM_CAPTURE_SCAN_RETRY ',
            lambda:(self.base/'stop').write_text('stop')))])
        self.assertEqual(rc,0,out)
        self.assertIn('reason=stop_file',out)
        self.assertEqual(self.jobs(),[],out)
        self.assertTrue((self.base/'scan-commands').read_text().endswith('METADATA_FAULT\n'),out)

    def test_on_dispatches_live_preset_after_monitor_is_reaped(self):
        rc, out = self.run_daemon(events=[(.04, lambda _: self.new())])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.jobs()), 1, out)
        job = self.jobs()[0]
        self.assertEqual(job['source'], 'A:/DCIM/10061115/A6400002.JPG')
        self.assertEqual(job['settings'], ON.strip())
        self.assertRegex(job['target'], r'^A:/A7R4A/TEST/F\d{7}\.JPG$')
        self.assertFalse((self.base / 'inflight').exists())
        self.assertFalse((self.base / 'job.lock').exists())
        self.assertIn('FILM_CAPTURE_JOB_DONE', out)

    def test_cold_root_version_record_still_dispatches_new_photo_once(self):
        (self.base/'recorded-version').write_bytes(SYNTHETIC_ROOT_VERSION)
        rc,out=self.run_daemon(monitor_fault='wd_version_recorded',
                               events=[(.04,lambda _:self.new())])
        self.assertEqual(rc,0,out)
        self.assertEqual(len(self.jobs()),1,out)
        self.assertEqual(self.jobs()[0]['source'],'A:/DCIM/10061115/A6400002.JPG')
        self.assertIn('FILM_CAPTURE_JOB_DONE result=0 ',out)

    def test_getstill_callback_still_dispatches_new_photo_exactly_once(self):
        rc,out=self.run_daemon(monitor_fault='getstill_live',
                               events=[(.04,lambda _:self.new())])
        self.assertEqual(rc,0,out)
        self.assertEqual(len(self.jobs()),1,out)
        self.assertEqual(self.jobs()[0]['source'],'A:/DCIM/10061115/A6400002.JPG')
        self.assertIn('FILM_CAPTURE_JOB_DONE result=0 ',out)

    def test_cache_playback_notice_still_dispatches_new_photo_exactly_once(self):
        rc,out=self.run_daemon(monitor_fault='cache_playback_live',
                               events=[(.04,lambda _:self.new())])
        self.assertEqual(rc,0,out)
        self.assertEqual(len(self.jobs()),1,out)
        self.assertEqual(self.jobs()[0]['source'],'A:/DCIM/10061115/A6400002.JPG')
        self.assertIn('FILM_CAPTURE_JOB_DONE result=0 ',out)

    def test_off_candidates_are_not_processed_later_when_on(self):
        rc, out = self.run_daemon(settings='FILM_OFF\n', events=[
            (.04, lambda _: self.new()),
            (.06, self.after_record('manifest', 'state=off ',
                                  lambda: (self.base / 'settings').write_text(ON)))])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.jobs(), [], out)
        self.assertIn('FILM_CAPTURE_SKIP_OFF', out)

    def test_failed_job_diagnostic_survives_next_job_log_reuse(self):
        rc, out = self.run_daemon(fault='failed_first', events=[
            (.04, lambda _: self.new()),
            (.06, self.after_record('manifest',
                'state=failed source=A:/DCIM/10061115/A6400002.JPG ',
                lambda: self.new(3)))])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.jobs()), 2, out)
        records = [dict(field.split('=', 1) for field in line.split()[1:])
                   for line in out.splitlines() if line.startswith('FILM_CAPTURE_JOB_DIAG ')]
        self.assertEqual(len(records), 2, out)
        self.assertEqual(records[0]['exit'], '12')
        self.assertEqual(records[0]['path'], 'ms:/DCIM/10061115/A6400002.JPG')
        self.assertIn(b'RAM_JOB_FAIL stage=settings_changed',
                      bytes.fromhex(records[0]['tail_hex']))
        self.assertEqual(records[1]['exit'], '0')
        self.assertIn(b'RAM_JOB_APPLIED ', bytes.fromhex(records[1]['tail_hex']))
        self.assertGreater(int(records[0]['uptime_ms']), 0)
        self.assertGreaterEqual(int(records[1]['uptime_ms']), int(records[0]['uptime_ms']))
        self.assertIn('applied=1 off=0 failed=1 retained=0', out)

    def test_failed_job_diagnostic_tail_is_bounded_and_escape_safe(self):
        rc, out = self.run_daemon(fault='failed_flood', events=[(.04, lambda _: self.new())])
        self.assertEqual(rc, 0, out)
        record = next((line for line in out.splitlines()
                       if line.startswith('FILM_CAPTURE_JOB_DIAG ')), '')
        self.assertTrue(record, out)
        fields = dict(field.split('=', 1) for field in record.split()[1:])
        self.assertGreater(int(fields['log_bytes']), 4096)
        tail = bytes.fromhex(fields['tail_hex'])
        self.assertEqual(len(tail), 512)
        self.assertIn(b'\x1b[31m\nRAM_JOB_FAIL stage=settings_changed', tail)
        self.assertNotIn('\x1b', out)
        self.assertLess(len(record), 2048)

    def test_off_marks_entire_pending_batch_before_later_on(self):
        rc, out = self.run_daemon(settings='FILM_OFF\n', events=[
            (.04, lambda _: (self.new(2), self.new(3))),
            (.06, self.after_record('manifest', 'state=off ',
                                  lambda: (self.base / 'settings').write_text(ON)))])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.jobs(), [], out)
        self.assertEqual(out.count('FILM_CAPTURE_SKIP_OFF '), 2, out)

    def test_unavailable_never_dispatches_until_fresh_on(self):
        rc, out = self.run_daemon(settings='FILM_UNAVAILABLE source=1\n', events=[
            (.04, lambda _: self.new()),
            (.06, self.after_record('settings-observed', 'FILM_UNAVAILABLE ',
                                  lambda: (self.base / 'settings').write_text(ON)))])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.jobs()), 1, out)
        self.assertIn('FILM_CAPTURE_SETTINGS_UNAVAILABLE', out)
        self.assertIn('cli_source=1 cli_reason=snapshot_inactive_or_stale ', out)

    def settings_diagnostics(self, out):
        records=[dict(part.split('=',1) for part in line.split()[1:])
                 for line in out.splitlines() if line.startswith('FILM_CAPTURE_SETTINGS_DIAG ')]
        self.assertTrue(records,out)
        for fields in records:
            self.assertLessEqual(int(fields['stdout_bytes']),127,out)
            self.assertEqual(len(fields['stdout_hex']),2*int(fields['stdout_bytes']),out)
            self.assertLess(len(' '.join(fields.values())),650,out)
        return records

    def stop_after_settings(self):
        return self.after_record('settings-observed','',lambda:(self.base/'stop').touch())

    def test_settings_unavailable_preserves_source_exit_and_escaped_stdout(self):
        text='FILM_UNAVAILABLE source=-13\n'
        rc,out=self.run_daemon(settings=text,events=[(.04,lambda _:self.new()),
                            (.06,self.stop_after_settings())])
        self.assertEqual(rc,0,out);self.assertEqual(self.jobs(),[],out)
        fields=self.settings_diagnostics(out)[0]
        self.assertEqual((fields['reason'],fields['exit'],fields['has_source'],
                          fields['cli_source'],fields['cli_reason']),
                         ('cli_exit','11','1','-13','module_maps'),out)
        self.assertEqual(bytes.fromhex(fields['stdout_hex']).decode(),text)

    def test_invalid_settings_stdout_is_diagnostic_and_never_dispatches(self):
        cases=[('', 'stdout_empty'),
               (ON.rstrip('\n'),'stdout_unterminated'),
               (ON.replace('kodak_gold','unknown'),'job_preset'),
               (ON.replace('grain=2','grain=02'),'job_noncanonical'),
               (ON+'\x1b[31mBAD\n','job_format'),
               ('x'*200+'\n','stdout_capacity')]
        for text,reason in cases:
            with self.subTest(reason=reason):
                rc,out=self.run_daemon(settings=text,events=[(.04,lambda _:self.new()),
                                    (.06,self.stop_after_settings())])
                self.assertEqual(rc,0,out);self.assertEqual(self.jobs(),[],out)
                fields=self.settings_diagnostics(out)[0]
                self.assertEqual(fields['reason'],reason,out)
                self.assertNotIn('\x1b',out)
                for file in ('stop','manifest','settings-started','settings-observed'):
                    (self.base/file).unlink(missing_ok=True)
                (self.dcim/'A6400002.JPG').unlink(missing_ok=True)

    def test_settings_child_exit_and_signal_are_distinguished(self):
        for fault,reason,code,sig in [('exit127','stdout_empty','127','0'),
                                    ('signal','child_signal','-1',str(int(signal.SIGTERM)))]:
            with self.subTest(fault=fault):
                rc,out=self.run_daemon(settings_fault=fault,events=[(.04,lambda _:self.new()),
                                    (.06,self.stop_after_settings())])
                self.assertEqual(rc,0,out);self.assertEqual(self.jobs(),[],out)
                fields=self.settings_diagnostics(out)[0]
                self.assertEqual((fields['reason'],fields['exit'],fields['signal']),
                                 (reason,code,sig),out)
                for file in ('stop','manifest','settings-started','settings-observed'):
                    (self.base/file).unlink(missing_ok=True)
                (self.dcim/'A6400002.JPG').unlink(missing_ok=True)

    def test_settings_timeout_is_bounded_and_never_dispatches(self):
        rc,out=self.run_daemon(settings_delay='2',events=[(.04,lambda _:self.new())])
        self.assertEqual(rc,0,out);self.assertEqual(self.jobs(),[],out)
        fields=self.settings_diagnostics(out)[0]
        self.assertEqual((fields['reason'],fields['wait_done']),('cli_timeout','0'),out)
        self.assertGreaterEqual(int(fields['elapsed_ms']),1000,out)

    def test_changed_live_cli_pin_has_local_reason_without_execution(self):
        def change_and_capture(_):
            self.settings.write_text(SETTINGS+'\n# changed after baseline\n')
            self.new()
        rc,out=self.run_daemon(events=[(.04,change_and_capture)])
        self.assertEqual(rc,0,out);self.assertEqual(self.jobs(),[],out)
        self.assertFalse((self.base/'settings-started').exists(),out)
        fields=self.settings_diagnostics(out)[0]
        self.assertEqual((fields['reason'],fields['child_pid'],fields['has_source']),
                         ('cli_pin','0','0'),out)

    def test_menu_close_does_not_cancel_enabled_setting(self):
        (self.base / 'menu-visible').write_text('0')
        rc, out = self.run_daemon(events=[(.04, lambda _: self.new())])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.jobs()), 1, out)

    def test_each_dispatch_uses_fresh_selected_film(self):
        second = 'FILM_JOB preset=tri_x_400 grain=1 halation=0\n'
        def second_photo():
            (self.base / 'settings').write_text(second)
            self.new(3)
        rc, out = self.run_daemon(events=[(.03, lambda _: self.new(2)),
            (.05, self.after_record('manifest',
                'state=applied source=A:/DCIM/10061115/A6400002.JPG ', second_photo))])
        self.assertEqual(rc, 0, out)
        self.assertEqual([job['settings'] for job in self.jobs()],
                         [ON.strip(), second.strip()], out)

    def test_stop_during_readonly_gate_does_not_start_new_job(self):
        def stop_after_gate_started(_):
            deadline = time.monotonic() + 2
            while not (self.base / 'settings-started').exists() and time.monotonic() < deadline:
                time.sleep(.005)
            (self.base / 'stop').write_text('stop')
        rc, out = self.run_daemon(events=[(.04, lambda _: self.new()),
                     (.06, stop_after_gate_started)], settings_delay='.2')
        self.assertTrue((self.base / 'settings-started').exists(), out)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.jobs(), [], out)
        self.assertFalse((self.base / 'inflight').exists())

    def test_pending_queue_survives_job_and_new_photos_during_render(self):
        rc, out = self.run_daemon(events=[(.03, lambda _: self.new(2)),
            (.05, self.after_record('job-started', 'started',
                                   lambda: (self.new(3), self.new(4))))], job_delay='.2')
        self.assertEqual(rc, 0, out)
        jobs = self.jobs()
        self.assertEqual([j['source'] for j in jobs],
                         [f'A:/DCIM/10061115/A640{i:04d}.JPG' for i in (2, 3, 4)], out)
        self.assertEqual(len({j['target'] for j in jobs}), 3)

    def assert_job_stops_dispatch(self, fault):
        rc, out = self.run_daemon(fault=fault, events=[
            (.03, lambda _: (self.new(2), self.new(3)))])
        self.assertEqual(rc, 21, out)
        self.assertEqual(len(self.jobs()), 1, out)
        self.assertTrue((self.base / 'capture.lock').is_file())
        self.assertTrue((self.base / 'inflight').is_file())
        self.assertIn('uncertain', (self.base / 'manifest').read_text())

    def test_uncertain_job_stops_dispatch_and_preserves_receipts(self):
        self.assert_job_stops_dispatch('uncertain')

    def test_retained_job_lock_stops_dispatch_and_preserves_receipts(self):
        self.assert_job_stops_dispatch('retained')

    def test_zero_exit_without_applied_receipt_is_not_accepted(self):
        self.assert_job_stops_dispatch('missing_receipt')

    def test_zero_exit_with_unverified_saved_target_is_not_accepted(self):
        self.assert_job_stops_dispatch('unverified_target')

    def test_stop_during_job_waits_for_completed_job(self):
        def stop_when_running(_):
            deadline = time.monotonic() + 2
            while not (self.base / 'job-started').exists() and time.monotonic() < deadline:
                time.sleep(.005)
            (self.base / 'stop').write_text('stop')
        rc, out = self.run_daemon(events=[(.04, lambda _: self.new()),
                     (.06, stop_when_running)], job_delay='.3')
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.jobs()), 1, out)
        self.assertIn('FILM_CAPTURE_JOB_DONE', out)
        self.assertFalse((self.base / 'inflight').exists())
        self.assertFalse((self.base / 'capture.lock').exists())

    def test_signal_during_job_waits_and_does_not_kill_native_job(self):
        def signal_when_running(process):
            deadline = time.monotonic() + 2
            while not (self.base / 'job-started').exists() and time.monotonic() < deadline:
                time.sleep(.005)
            process.send_signal(signal.SIGTERM)
        rc, out = self.run_daemon(events=[(.04, lambda _: self.new()),
                     (.06, signal_when_running)], job_delay='.3')
        self.assertEqual(rc, 20, out)
        self.assertEqual(len(self.jobs()), 1, out)
        self.assertIn('FILM_CAPTURE_JOB_DONE', out)
        self.assertFalse((self.base / 'inflight').exists())

    def test_changed_script_or_cli_pin_refuses_before_monitor_start(self):
        for path in (self.settings, self.script):
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            rc, out = self.run_daemon()
            self.assertEqual(rc, 3, out)
            self.assertFalse((self.base / 'monitor.pid').exists())
            path.write_bytes(original)

    def test_baseline_capacity_fails_explicitly_without_dispatch(self):
        for number in range(2, 129):
            (self.dcim / f'A640{number:04d}.JPG').write_bytes(b'old')
        rc, out = self.run_daemon()
        self.assertNotEqual(rc, 0, out)
        self.assertIn('baseline_capacity', out)
        self.assertEqual(self.jobs(), [])

    def test_unconfirmed_mpr_scan_stops_and_retains_global_lock(self):
        rc, out = self.run_daemon(monitor_fault='no_prompt')
        self.assertEqual(rc, 21, out)
        self.assertEqual(self.jobs(), [])
        self.assertTrue((self.base / 'capture.lock').exists())

    def test_initial_confirmed_unmounted_waits_for_first_real_baseline(self):
        flag = self.base / 'media-unmounted'
        flag.write_text('unmounted')
        def mount_after_confirmed_wait(_):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                path = self.base / 'manifest'
                if path.exists() and 'phase=baseline_media_wait\n' in path.read_text():
                    flag.unlink()
                    return
                time.sleep(.005)
        rc, out = self.run_daemon(ready_events=[(.01, mount_after_confirmed_wait)],
                                 events=[(.04, lambda _: self.new())])
        self.assertEqual(rc, 0, out)
        self.assertIn('FILM_CAPTURE_BASELINE_MEDIA_WAIT', out)
        self.assertIn('FILM_CAPTURE_BASELINE count=1', out)
        self.assertEqual([job['source'] for job in self.jobs()],
                         ['A:/DCIM/10061115/A6400002.JPG'], out)

    def test_initial_confirmed_unmounted_expiry_never_accepts_empty_baseline(self):
        rc, out = self.run_daemon(monitor_fault='media_unmounted')
        self.assertEqual(rc, 1, out)
        self.assertIn('reason=baseline_media_timeout', out)
        self.assertNotIn('FILM_CAPTURE_BASELINE count=', out)
        self.assertEqual(self.jobs(), [])
        self.assertFalse((self.base / 'capture.lock').exists())

    def recorded_midscan_unmounted(self, location):
        (self.base/'recorded-unmounted').write_bytes(SYNTHETIC_FNEXT_UNMOUNTED)
        (self.base/'midscan-unmounted').write_text(location)

    def test_partial_baseline_waits_then_excludes_existing_photos(self):
        self.recorded_midscan_unmounted('sub_next')
        def restore(_):
            manifest=(self.base/'manifest').read_text()
            self.assertIn('phase=baseline_media_wait\n',manifest)
            self.assertIn('count=0\n',manifest)
            self.assertEqual(self.jobs(),[])
            (self.base/'midscan-unmounted').unlink()
        rc,out=self.run_daemon(ready_events=[(.01,self.after_record('manifest','phase=baseline_media_wait',lambda:restore(None)))],
                               events=[(.04,lambda _:self.new())])
        self.assertEqual(rc,0,out)
        self.assertIn('FILM_CAPTURE_BASELINE count=1',out)
        self.assertEqual([job['source'] for job in self.jobs()],['A:/DCIM/10061115/A6400002.JPG'],out)

    def test_midscan_media_loss_discards_partial_scan_and_restarts_root_before_dispatch(self):
        (self.base/'recorded-unmounted').write_bytes(SYNTHETIC_FNEXT_UNMOUNTED)
        def lose_media(_):
            (self.base/'midscan-unmounted').write_text('sub_next')
            self.new()
        def restore():
            time.sleep(.15)  # Longer than the real daemon's stability gate.
            self.assertEqual(self.jobs(),[])
            manifest=(self.base/'manifest').read_text()
            self.assertIn('count=1\n',manifest)  # Only the completed baseline.
            commands=(self.base/'unmount-commands').read_text().splitlines()
            self.assertEqual(commands[-1],'UNMOUNTED')
            (self.base/'midscan-unmounted').unlink()
        rc,out=self.run_daemon(events=[(.04,lose_media),(.06,self.after_record('manifest','phase=media_unavailable',restore))])
        self.assertEqual(rc,0,out)
        self.assertEqual([job['source'] for job in self.jobs()],['A:/DCIM/10061115/A6400002.JPG'],out)
        commands=(self.base/'unmount-commands').read_text().splitlines()
        interruptions=[i for i,value in enumerate(commands) if value=='UNMOUNTED']
        self.assertTrue(interruptions)
        for i in interruptions:
            self.assertEqual(commands[i+1],'MPR FileIf FFirst ms:/DCIM/*')
        self.assertEqual(out.count('FILM_CAPTURE_DISPATCH '),1,out)
        self.assertFalse((self.base/'capture.lock').exists())

    def test_latest_dir_only_skips_older_dcf_directories_and_rolls_over(self):
        newer_dir = self.base / 'DCIM/10161116'
        newer_dir.mkdir()
        (newer_dir / 'A6400002.JPG').write_bytes(b'baseline newer')
        self.build_daemon(
            extra_probe=('#undef CAPTURE_WAIT_MS', '#define CAPTURE_WAIT_MS 0u'),
            extra_build=('#define FILM_CAPTURE_LATEST_DIR_ONLY 1',),
        )
        roll_dir = self.base / 'DCIM/10261117'
        def add_in_current(_):
            (newer_dir / 'A6400003.JPG').write_bytes(b'new current shot')
        def rollover_folder():
            roll_dir.mkdir(exist_ok=True)
            (roll_dir / 'A6400004.JPG').write_bytes(b'new rollover shot')
        rc, out = self.run_daemon(
            events=[
                (.03, add_in_current),
                (.05, self.after_record('manifest', 'state=applied source=A:/DCIM/10161116/A6400003.JPG ', rollover_folder)),
            ])
        self.assertEqual(rc, 0, out)
        self.assertIn('FILM_CAPTURE_BASELINE count=1', out)
        self.assertIn('FILM_CAPTURE_FAST latest=1 ', out)
        commands = (self.base / 'scan-commands').read_text()
        self.assertIn('MPR FileIf FFirst ms:/DCIM/10161116/*', commands)
        self.assertNotIn('MPR FileIf FFirst ms:/DCIM/10061115/*', commands)
        self.assertEqual([job['source'] for job in self.jobs()],
                         ['A:/DCIM/10161116/A6400003.JPG', 'A:/DCIM/10261117/A6400004.JPG'], out)
        manifest = (self.base / 'manifest').read_text()
        self.assertNotIn('A:/DCIM/10161116/A6400002.JPG', manifest)
        self.assertIn('state=applied source=A:/DCIM/10161116/A6400003.JPG', manifest)

    def test_latest_dir_only_handles_more_than_max_dirs_folders(self):
        for n in range(101, 113):
            d = self.base / f'DCIM/{n}60515'
            d.mkdir()
            (d / 'A6400001.JPG').write_bytes(b'old photo')
        latest_dir = self.base / 'DCIM/11260515'
        self.build_daemon(
            extra_probe=('#undef CAPTURE_WAIT_MS', '#define CAPTURE_WAIT_MS 0u'),
            extra_build=('#define FILM_CAPTURE_LATEST_DIR_ONLY 1',),
        )
        rc, out = self.run_daemon(events=[(.03, lambda _: (latest_dir / 'A6400002.JPG').write_bytes(b'new shot'))])
        self.assertEqual(rc, 0, out)
        self.assertIn('FILM_CAPTURE_BASELINE count=1', out)
        self.assertEqual([job['source'] for job in self.jobs()], ['A:/DCIM/11260515/A6400002.JPG'], out)

    def test_latest_dir_only_handles_more_than_max_files_in_folder(self):
        for n in range(2, 141):
            (self.dcim / f'A640{n:04d}.JPG').write_bytes(b'old photo')
        self.build_daemon(
            extra_probe=('#undef CAPTURE_WAIT_MS', '#define CAPTURE_WAIT_MS 0u'),
            extra_build=('#define FILM_CAPTURE_LATEST_DIR_ONLY 1',),
        )
        rc, out = self.run_daemon(events=[(.05, lambda _: (self.dcim / 'A6400141.JPG').write_bytes(b'new shot'))])
        self.assertEqual(rc, 0, out)
        self.assertIn('FILM_CAPTURE_BASELINE count=128', out)
        self.assertEqual([job['source'] for job in self.jobs()], ['A:/DCIM/10061115/A6400141.JPG'], out)

    def test_proc_sd_trigger_wakes_before_heartbeat_when_io_settles(self):
        proc_dir = self.base / 'fakeproc'
        proc_dir.mkdir()
        irqs = proc_dir / 'interrupts'
        irqs.write_text('218:   10  0  0  0  GIC  mmc0\n227:  100  0  0  0  GIC  emmc0\n')
        self.build_daemon(
            extra_probe=('#undef CAPTURE_STABLE_MS', '#define CAPTURE_STABLE_MS 40u'),
            extra_build=(
                '#define FILM_CAPTURE_PROC_TRIGGER 1',
                '#define FILM_CAPTURE_IDLE_HEARTBEAT_MS 4500u',
                '#define FILM_CAPTURE_PROC_TICK_MS 20u',
                '#define FILM_CAPTURE_PROC_QUIET_TICKS 2u',
                f'#define FILM_CAPTURE_PROC_INTERRUPTS "{irqs}"',
            ),
        )
        def emmc_noise(_):
            irqs.write_text('218:   10  0  0  0  GIC  mmc0\n227:  900  0  0  0  GIC  emmc0\n')
        def burst_write(_):
            self.new()
            irqs.write_text('218:   50  0  0  0  GIC  mmc0\n227: 1500  0  0  0  GIC  emmc0\n')
        rc, out = self.run_daemon(events=[(.02, emmc_noise), (.06, burst_write)])
        self.assertEqual(rc, 0, out)
        self.assertIn('FILM_CAPTURE_FAST latest=0 proc=1 ', out)
        self.assertEqual([job['source'] for job in self.jobs()], ['A:/DCIM/10061115/A6400002.JPG'], out)

    def test_proc_mmc_pci_trigger_wakes_before_heartbeat_and_ignores_emmc(self):
        proc_dir = self.base / 'fakeproc_pci'
        proc_dir.mkdir()
        irqs = proc_dir / 'interrupts'
        irqs.write_text('206:   0  0  0  0  GIC  ms\n227:  100  0  0  0  GIC  emmc0\n240:  1000  0  0  0  GIC  mmc-pci\n')
        self.build_daemon(
            extra_probe=('#undef CAPTURE_STABLE_MS', '#define CAPTURE_STABLE_MS 40u'),
            extra_build=(
                '#define FILM_CAPTURE_PROC_TRIGGER 1',
                '#define FILM_CAPTURE_IDLE_HEARTBEAT_MS 4500u',
                '#define FILM_CAPTURE_PROC_TICK_MS 20u',
                '#define FILM_CAPTURE_PROC_QUIET_TICKS 2u',
                f'#define FILM_CAPTURE_PROC_INTERRUPTS "{irqs}"',
            ),
        )
        def emmc_noise(_):
            irqs.write_text('206:   0  0  0  0  GIC  ms\n227:  900  0  0  0  GIC  emmc0\n240:  1000  0  0  0  GIC  mmc-pci\n')
        def pci_burst_write(_):
            self.new()
            irqs.write_text('206:   0  0  0  0  GIC  ms\n227: 1500  0  0  0  GIC  emmc0\n240:  1600  0  0  0  GIC  mmc-pci\n')
        rc, out = self.run_daemon(events=[(.02, emmc_noise), (.06, pci_burst_write)])
        self.assertEqual(rc, 0, out)
        self.assertEqual([job['source'] for job in self.jobs()], ['A:/DCIM/10061115/A6400002.JPG'], out)

    def test_slot_b_source_dispatches_to_b_target(self):
        slotb_mon = self.base / 'monitor-slotb'
        slotb_mon.write_text(MONITOR.replace("args[3][4:]", "args[3][len('/SlotB:'):]").replace("ms:/DCIM", "/SlotB:/DCIM"))
        slotb_mon.chmod(0o700)
        slotb_dir = self.base / 'slotb_build'
        slotb_dir.mkdir()
        (slotb_dir / 'capture_probe_build.h').write_text(
            f'#define CAPTURE_MONITOR "{slotb_mon}"\n'
            f'#define CAPTURE_LOCK "{self.base}/slotb-capture.lock"\n'
            f'#define CAPTURE_STOP_FILE "{self.base}/stop"\n'
            f'#define CAPTURE_MONITOR_BYTES {slotb_mon.stat().st_size}u\n'
            f'#define CAPTURE_MONITOR_FNV 0x{fnv(slotb_mon.read_bytes()):08x}u\n'
            '#define CAPTURE_MPR_ROOT "/SlotB:/DCIM"\n'
            '#define CAPTURE_STARTUP_MS 100u\n#define CAPTURE_COMMAND_MS 1000u\n'
            '#define CAPTURE_PROMPT_QUIET_MS 20u\n'
            '#define CAPTURE_WAIT_MS 10u\n#define CAPTURE_ACTIVE_MS 2200u\n'
            '#define CAPTURE_INTERVAL_MS 20u\n#define CAPTURE_STABLE_MS 80u\n')
        (slotb_dir / 'film_capture_daemon_build.h').write_text(
            (self.base / 'film_capture_daemon_build.h').read_text())
        slotb_bin = self.base / 'daemon-slotb'
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', str(slotb_dir), '-I', str(ROOT / 'camera'),
                        str(SOURCE), str(ROOT / 'camera/film_runtime_log.c'),
                        '-o', str(slotb_bin)], check=True, capture_output=True)
        self.binary = slotb_bin
        rc, out = self.run_daemon(events=[(.04, lambda _: self.new())])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.jobs()), 1, out)
        job = self.jobs()[0]
        self.assertEqual(job['source'], 'B:/DCIM/10061115/A6400002.JPG')
        self.assertRegex(job['target'], r'^B:/A7R4A/TEST/F\d{7}\.JPG$')

    def test_persistent_daemon_recovers_from_root_playback_and_transient_corruption(self):
        self.build_daemon(
            extra_probe=('#undef CAPTURE_WAIT_MS', '#define CAPTURE_WAIT_MS 0u'),
            extra_build=('#undef FILM_CAPTURE_PERSISTENT', '#define FILM_CAPTURE_PERSISTENT 1',
                         '#define FILM_CAPTURE_LATEST_DIR_ONLY 1'),
        )
        rc, out = self.run_daemon(
            events=[
                (.03, lambda _: self.corrupt_metadata('always')),
                (.22, lambda _: ((self.base / 'corrupt-metadata').unlink() if (self.base / 'corrupt-metadata').exists() else None, self.new())),
                (.85, lambda _: (self.base / 'stop').write_text('stop')),
            ],
        )
        self.assertEqual(rc, 0, out)
        self.assertEqual([job['source'] for job in self.jobs()], ['A:/DCIM/10061115/A6400002.JPG'], out)

    def test_persistent_daemon_recovers_from_baseline_overflow(self):
        self.build_daemon(
            extra_probe=('#undef CAPTURE_WAIT_MS', '#define CAPTURE_WAIT_MS 0u'),
            extra_build=('#undef FILM_CAPTURE_PERSISTENT', '#define FILM_CAPTURE_PERSISTENT 1',
                         '#define FILM_CAPTURE_LATEST_DIR_ONLY 1'),
        )
        self.new(3)
        self.corrupt_metadata()
        (self.base / 'metadata-next-fault').write_text('overflow')
        rc, out = self.run_daemon(
            ready_events=[
                (.35, lambda _: (self.base / 'metadata-next-fault').unlink() if (self.base / 'metadata-next-fault').exists() else None),
            ],
            events=[
                (.05, lambda _: self.new(4)),
                (.35, lambda _: (self.base / 'stop').write_text('stop')),
            ],
        )
        self.assertEqual(rc, 0, out)
        self.assertIn('FILM_CAPTURE_BASELINE_TRANSPORT_RETRY retry=1', out)
        self.assertIn('FILM_CAPTURE_BASELINE count=2', out)
        self.assertEqual([job['source'] for job in self.jobs()], ['A:/DCIM/10061115/A6400004.JPG'], out)

    def test_v39_media_unavailable_releases_monitor_and_recovers(self):
        self.build_daemon(
            extra_probe=('#undef CAPTURE_WAIT_MS', '#define CAPTURE_WAIT_MS 0u'),
            extra_build=('#undef FILM_CAPTURE_PERSISTENT', '#define FILM_CAPTURE_PERSISTENT 1',
                         '#define FILM_CAPTURE_LATEST_DIR_ONLY 1',
                         '#define FILM_CAPTURE_MEDIA_UNAVAILABLE_PAUSE_MS 80u'),
        )
        observed = {}

        def check_monitor_stopped():
            pid = int((self.base / 'monitor.pid').read_text())
            try:
                os.kill(pid, 0)
                observed['alive'] = True
            except ProcessLookupError:
                observed['alive'] = False

        rc, out = self.run_daemon(
            events=[
                (.03, lambda _: (self.base / 'media-unmounted').write_text('1')),
                (.18, lambda _: check_monitor_stopped()),
                (.20, lambda _: ((self.base / 'media-unmounted').unlink(), self.new(5))),
                (.85, lambda _: (self.base / 'stop').write_text('stop')),
            ],
        )
        self.assertEqual(rc, 0, out)
        self.assertFalse(observed.get('alive', True), 'mpr_monio monitor stayed alive during media_unavailable')
        self.assertEqual([job['source'] for job in self.jobs()], ['A:/DCIM/10061115/A6400005.JPG'], out)


if __name__ == '__main__':
    unittest.main(verbosity=2)
