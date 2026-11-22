"""Process tests for one serialized command; only the Sony monitor is replaced.

NATIVE_RETURN deliberately does not establish copy success. The real caller must
verify copied bytes and JPEG integrity before dispatch or accepting an SD write.
"""
import os
from pathlib import Path
import signal
import struct
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'camera/film_native_command.c'
CAPTURE = 'A:/DCIM/10061115/A6409686.JPG'
TARGET = 'A:/A6400/TEST/F1234567.JPG'

MONITOR = r'''#!/usr/bin/env python3
import os,sys,time
from pathlib import Path
root=Path(os.environ['FILM_NATIVE_FIXTURE'])
fault=os.environ.get('FILM_NATIVE_FAULT','')
(root/'monitor.pid').write_text(str(os.getpid()))
if fault!='startup_empty':print('old error history\nMWF>',flush=True)
if fault=='startup_late_history':
 time.sleep(.015);print('late old history\nMWF>',flush=True)
 (root/'last-noise').write_text(str(time.monotonic()))
if fault=='startup_never_quiet':
 for i in range(100):
  print('startup noise',flush=True);time.sleep(.01)
for request in sys.stdin:
 command=request.rstrip('\n')
 (root/'command').write_text(command)
 (root/'command-at').write_text(str(time.monotonic()))
 if fault in ('pre_echo_overflow','pre_echo_then_fresh_overflow','absent_old_overflow'):
  print('[IF_DB] NotLoad\n###OVERFLOW###',flush=True)
 if fault=='absent_stale':
  print("[IFSYS]Can't open file "+command.split()[2],flush=True)
 if fault=='pre_echo_large':print(('old history '*1000+'\n')*4,flush=True)
 if fault=='pre_echo_capacity':print('x'*140000+'\n',flush=True)
 if fault=='pre_echo_suffix_trap':
  print('x'*33000+command+'\nMWF>',flush=True);continue
 if fault=='pre_echo_fragmented':
  print('old ###OVERFLOW###',flush=True)
  sys.stdout.write(command[:12]);sys.stdout.flush();time.sleep(.04)
  print(command[12:]+'\nMWF>',flush=True);continue
 if fault=='prompt_before_echo':
  print('MWF>',flush=True);time.sleep(.12)
 if fault=='no_echo':
  print('historical '+command+'\nMWF>',flush=True);continue
 if fault=='partial_echo':
  print(command+' EXTRA\nMWF>',flush=True);continue
 print(('MWF>' if fault=='prefix_echo' else '')+command,flush=True)
 if fault.startswith('read_unavailable'):
  source=command.split()[2]
  if fault=='read_unavailable_wrong':source=source.replace('A6409686','A6409999')
  if fault=='read_unavailable_mwf_benign':
   print('[MWF]  [RsrcAcsr] Not acquired!',flush=True)
   print('[GetStillTargetMaxFileSize]target[44744704] max[58202112]',flush=True)
  print("[IFSYS]Can't open file "+source,flush=True)
  if fault=='read_unavailable_partial':(root/'input.jpg').write_bytes(b'partial')
  if fault=='read_unavailable_extra':print('unexpected extra diagnostic',flush=True)
  if fault!='read_unavailable_no_prompt':print('MWF>',flush=True)
  continue
 if fault.startswith('absent_') and fault!='absent_stale':
  expected="[IFSYS]Can't open file "+command.split()[2]
  if fault=='absent_wrong_target':expected=expected.replace('F1234567','F7654321')
  if fault=='absent_suffix':expected+=' EXTRA'
  if fault=='absent_mwf_benign':
   print('[MWF]  [RsrcAcsr] Not acquired!',flush=True)
   print('[MWF]  [RsrcAcsr] already Acquired!',flush=True)
   print('[GetStillTargetMaxFileSize]target[44744704] max[58202112]',flush=True)
  if fault=='absent_mwf_error':
   print('[MWF] ERROR unexpected failure',flush=True)
  if fault=='absent_incomplete':
   print(expected+'MWF>',flush=True);continue
  print(expected,flush=True)
  if fault=='absent_duplicate':print(expected,flush=True)
  if fault=='absent_native_error':print('native ERROR 0x123',flush=True)
  if fault=='absent_lower_error':print('native error writing destination',flush=True)
  if fault=='absent_partial_input':(root/'input.jpg').write_bytes(b'partial')
  if fault=='absent_fresh_overflow':print('###OVERFLOW###',flush=True)
  if fault=='absent_no_prompt':continue
  print('MWF>',flush=True);continue
 if fault=='no_prompt':continue
 if fault=='dies':sys.exit(0)
 if fault in ('overflow','pre_echo_then_fresh_overflow'):
  print('MON RING OVERFLOW\nMWF>',flush=True);continue
 if fault=='overflow_between_echoes':
  print('[IF_DB] NotLoad\n###OVERFLOW###\n'+command+'\r\nMWF>',flush=True);continue
 if fault=='reanchor_mwf_pre_exec_error':
  print('[SynthMgr] background async status line',flush=True)
  print('[SynthCnt][0x1000][ERROR] AsyncBackgroundNotice : [00000001]',flush=True)
  print('[SynthFsys][0x1000][ERROR] QueuedStatus : [00000002]',flush=True)
  print('[MWF] background diagnostic before execution',flush=True)
  sys.stdout.write(command+'\r\nMWF>');sys.stdout.flush();continue
 if fault=='reanchor_absent':
  print('[SynthFsys][0x1000][ERROR] QueuedStatus : [00000002]',flush=True)
  sys.stdout.write(command+'\r\n[IFSYS]Can\'t open file '+command.split()[2]+'\r\nMWF>');sys.stdout.flush();continue
 if fault=='reanchor_prefixed_chunked':
  print('[SynthFsys][0x1000][ERROR] QueuedStatus : [00000002]',flush=True)
  sys.stdout.write('MWF>'+command[:9]);sys.stdout.flush();time.sleep(.04)
  sys.stdout.write(command[9:]+'\r\nMWF>');sys.stdout.flush();continue
 if fault=='post_reanchor_error':
  sys.stdout.write(command+'\r\nnative ERROR 0x123\r\nMWF>');sys.stdout.flush();continue
 if fault=='reanchor_triple_echo_over_cap':
  print('x'*30000,flush=True)
  print(command,flush=True)
  print('x'*34000,flush=True)
  print(command+'\r\nMWF>',flush=True);continue
 if fault=='capacity':print('x'*33000,flush=True);continue
 if fault=='open_error':print("Can't open file "+command.split()[-1]+'\nMWF>',flush=True);continue
 if fault=='native_error':print('native ERROR 0x123\nMWF>',flush=True);continue
 if fault=='delayed_copy':
  time.sleep(.16);(root/'input.jpg').write_bytes(b'delayed complete file')
 if fault=='silent_copy_failure':
  print('MWF>',flush=True);continue
 if command.startswith('IFSYS flush ') and fault!='flush_marker_missing':
  print('Fsys_Flush OK',flush=True)
 print('MWF>',flush=True)
'''


def fnv(data):
    result = 2166136261
    for byte in data:
        result = ((result ^ byte) * 16777619) & 0xffffffff
    return result


class FilmNativeCommandTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCE.is_file(), 'bounded native command adapter is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.monitor = self.base / 'monitor'
        self.monitor.write_text(MONITOR)
        self.monitor.chmod(0o700)
        self.header = self.base / 'film_native_command_build.h'
        self.header.write_text(
            f'#define FILM_NATIVE_MONITOR "{self.monitor}"\n'
            f'#define FILM_NATIVE_INPUT "{self.base}/input.jpg"\n'
            f'#define FILM_NATIVE_OUTPUT "{self.base}/output.jpg"\n'
            f'#define FILM_NATIVE_MONITOR_BYTES {self.monitor.stat().st_size}u\n'
            f'#define FILM_NATIVE_MONITOR_FNV 0x{fnv(self.monitor.read_bytes()):08x}u\n'
            '#define FILM_NATIVE_STARTUP_MS 200u\n'
            '#define FILM_NATIVE_QUIET_MS 50u\n'
            '#define FILM_NATIVE_STARTUP_MAX_MS 800u\n'
            '#define FILM_NATIVE_COMMAND_MS 450u\n')
        self.binary = self.base / 'native'
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', str(self.base), str(SOURCE), '-o', str(self.binary)],
                       check=True, capture_output=True)

    def run_native(self, operation='read', path=CAPTURE, fault='', timeout=3):
        env = dict(os.environ, FILM_NATIVE_FIXTURE=str(self.base), FILM_NATIVE_FAULT=fault)
        return subprocess.run([str(self.binary), operation, path], env=env,
                              capture_output=True, text=True, timeout=timeout)

    def assert_child_reaped(self):
        marker = self.base / 'monitor.pid'
        if marker.exists():
            with self.assertRaises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)

    def test_real_read_command_and_return_only_claim(self):
        result = self.run_native()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.base / 'command').read_text(),
                         f'IFSYS cp {CAPTURE} {self.base}/input.jpg')
        self.assertIn('NATIVE_RETURN', result.stdout)
        self.assertIn('copy_verified=0', result.stdout)
        self.assertIn('uncertain=0', result.stdout)
        self.assert_child_reaped()

    def test_exact_read_source_open_failure_can_be_deferred_without_input(self):
        result=self.run_native(fault='read_unavailable')
        self.assertEqual(result.returncode,14,result.stdout)
        self.assertIn('NATIVE_FAIL operation=read reason=source_unavailable',result.stdout)
        self.assertIn('uncertain=0',result.stdout)
        self.assertFalse((self.base/'input.jpg').exists())
        self.assert_child_reaped()

    def test_read_unavailable_is_not_inferred_from_other_or_partial_replies(self):
        for fault in ('read_unavailable_wrong','read_unavailable_extra',
                      'read_unavailable_partial','read_unavailable_no_prompt'):
            with self.subTest(fault=fault):
                (self.base/'input.jpg').unlink(missing_ok=True)
                result=self.run_native(fault=fault)
                self.assertNotEqual(result.returncode,14,result.stdout)
                self.assertNotIn('reason=source_unavailable',result.stdout)
                self.assert_child_reaped()

    def test_readback_open_failure_is_never_retryable_source_unavailability(self):
        result=self.run_native('readback',TARGET,fault='read_unavailable')
        self.assertNotEqual(result.returncode,14,result.stdout)
        self.assertNotIn('reason=source_unavailable',result.stdout)
        self.assert_child_reaped()

    def test_delayed_copy_finishes_before_return(self):
        start = time.monotonic()
        result = self.run_native(fault='delayed_copy')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertGreaterEqual(time.monotonic() - start, .16)
        self.assertEqual((self.base / 'input.jpg').read_bytes(), b'delayed complete file')
        self.assert_child_reaped()

    def test_prompt_before_fresh_echo_cannot_complete_early(self):
        start = time.monotonic()
        result = self.run_native(fault='prompt_before_echo')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertGreaterEqual(time.monotonic() - start, .12)

    def test_command_echo_can_follow_unterminated_prior_prompt(self):
        result = self.run_native(fault='prefix_echo')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('NATIVE_RETURN', result.stdout)

    def test_missing_exact_echo_or_prompt_is_uncertain(self):
        for fault in ('no_echo', 'partial_echo', 'no_prompt', 'dies'):
            with self.subTest(fault=fault):
                result = self.run_native(fault=fault)
                self.assertEqual(result.returncode, 21, result.stdout)
                self.assertIn('uncertain=1', result.stdout)
                self.assertNotIn('NATIVE_RETURN', result.stdout)
                self.assert_child_reaped()

    def test_fresh_native_errors_fail_after_command_return(self):
        for fault in ('open_error', 'native_error'):
            with self.subTest(fault=fault):
                result = self.run_native(fault=fault)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('uncertain=0', result.stdout)
                self.assertNotIn('NATIVE_RETURN', result.stdout)
                self.assert_child_reaped()

    def test_ring_or_response_overflow_is_uncertain_and_bounded(self):
        for fault in ('overflow', 'capacity'):
            with self.subTest(fault=fault):
                result = self.run_native(fault=fault)
                self.assertEqual(result.returncode, 21, result.stdout)
                self.assertIn('uncertain=1', result.stdout)
                self.assertLess(len(result.stdout), 2000)
                self.assert_child_reaped()

    def test_old_overflow_before_exact_echo_is_counted_not_a_command_failure(self):
        result = self.run_native(fault='pre_echo_overflow')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('NATIVE_RETURN', result.stdout)
        self.assertIn('pre_echo_overflows=1', result.stdout)
        self.assertNotIn('ring_overflow', result.stdout)

    def test_large_pre_echo_history_is_bounded_and_not_fresh_response(self):
        result = self.run_native(fault='pre_echo_large')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('NATIVE_RETURN', result.stdout)
        self.assertLess(len(result.stdout), 2000)

    def test_overflow_after_echo_still_fails_even_after_old_overflow(self):
        result = self.run_native(fault='pre_echo_then_fresh_overflow')
        self.assertEqual(result.returncode, 21, result.stdout)
        self.assertIn('reason=ring_overflow', result.stdout)
        self.assertIn('uncertain=1', result.stdout)
        self.assertIn('pre_echo_overflows=1', result.stdout)

    def test_pre_echo_total_history_cap_still_fails_uncertain(self):
        result = self.run_native(fault='pre_echo_capacity')
        self.assertEqual(result.returncode, 21, result.stdout)
        self.assertIn('reason=history_capacity', result.stdout)
        self.assertIn('uncertain=1', result.stdout)
        self.assertLess(len(result.stdout), 2000)

    def test_rolling_history_never_promotes_a_truncated_suffix_to_exact_echo(self):
        result = self.run_native(fault='pre_echo_suffix_trap')
        self.assertEqual(result.returncode, 21, result.stdout)
        self.assertIn('uncertain=1', result.stdout)
        self.assertNotIn('NATIVE_RETURN', result.stdout)

    def test_old_overflow_and_echo_split_across_reads(self):
        result = self.run_native(fault='pre_echo_fragmented')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('pre_echo_overflows=1', result.stdout)

    def test_startup_waits_for_quiet_after_late_history(self):
        result = self.run_native(fault='startup_late_history')
        self.assertEqual(result.returncode, 0, result.stdout)
        received = float((self.base / 'command-at').read_text())
        last_noise = float((self.base / 'last-noise').read_text())
        self.assertGreaterEqual(received - last_noise, .023)

    def test_empty_startup_ring_still_sends_and_requires_fresh_echo(self):
        result = self.run_native(fault='startup_empty')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('NATIVE_RETURN', result.stdout)
        self.assertIn('startup_history_bytes=0', result.stdout)
        self.assertEqual((self.base / 'command').read_text(),
                         f'IFSYS cp {CAPTURE} {self.base}/input.jpg')
        self.assert_child_reaped()

    def test_startup_never_quiet_refuses_before_sending(self):
        result = self.run_native(fault='startup_never_quiet')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('reason=startup_busy', result.stdout)
        self.assertIn('uncertain=0', result.stdout)
        self.assertNotIn('NATIVE_SENT', result.stdout)
        self.assertFalse((self.base / 'command').exists())
        self.assert_child_reaped()

    def test_readback_write_and_flush_have_fixed_scopes(self):
        for operation, expected in (
            ('readback', f'IFSYS cp {TARGET} {self.base}/input.jpg'),
            ('write', f'IFSYS cp {self.base}/output.jpg {TARGET}'),
            ('flush', f'IFSYS flush {TARGET}'),
        ):
            with self.subTest(operation=operation):
                result = self.run_native(operation, TARGET)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual((self.base / 'command').read_text(), expected)
                self.assertIn('copy_verified=0', result.stdout)

    def test_flush_requires_positive_native_success_marker(self):
        result = self.run_native('flush', TARGET, 'flush_marker_missing')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('uncertain=0', result.stdout)
        self.assertNotIn('NATIVE_RETURN', result.stdout)

    def test_absent_requires_exact_fresh_source_open_failure_without_input(self):
        result = self.run_native('absent', TARGET, 'absent_exact')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.base / 'command').read_text(),
                         f'IFSYS cp {TARGET} {self.base}/input.jpg')
        self.assertIn('NATIVE_ABSENT operation=absent reason=source_open_failed', result.stdout)
        self.assertIn('uncertain=0 copy_verified=0', result.stdout)
        self.assertNotIn('NATIVE_RETURN', result.stdout)
        self.assertFalse((self.base / 'input.jpg').exists())
        self.assert_child_reaped()

    def test_absent_rejects_any_preexisting_input_before_sending(self):
        path = self.base / 'input.jpg'
        for kind in ('file', 'directory', 'fifo', 'symlink'):
            with self.subTest(kind=kind):
                if kind == 'file':
                    path.write_bytes(b'partial or previous input')
                elif kind == 'directory':
                    path.mkdir()
                elif kind == 'fifo':
                    os.mkfifo(path)
                else:
                    path.symlink_to(self.base / 'missing-file')
                try:
                    result = self.run_native('absent', TARGET, 'absent_exact')
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertIn('uncertain=0', result.stdout)
                    self.assertNotIn('NATIVE_ABSENT', result.stdout)
                    self.assertFalse((self.base / 'command').exists())
                    self.assertFalse((self.base / 'monitor.pid').exists())
                finally:
                    path.rmdir() if kind == 'directory' else path.unlink()

    def test_absent_rejects_new_partial_input_even_with_expected_error(self):
        result = self.run_native('absent', TARGET, 'absent_partial_input')
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('uncertain=0', result.stdout)
        self.assertNotIn('NATIVE_ABSENT', result.stdout)
        self.assertEqual((self.base / 'input.jpg').read_bytes(), b'partial')
        self.assert_child_reaped()

    def test_absent_rejects_stale_or_missing_source_open_failure(self):
        for fault in ('absent_stale', 'silent_copy_failure'):
            with self.subTest(fault=fault):
                result = self.run_native('absent', TARGET, fault)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('uncertain=0', result.stdout)
                self.assertNotIn('NATIVE_ABSENT', result.stdout)
                self.assert_child_reaped()

    def test_absent_rejects_wrong_suffix_duplicate_or_additional_error(self):
        for fault in ('absent_wrong_target', 'absent_suffix', 'absent_incomplete',
                      'absent_duplicate', 'absent_native_error', 'absent_lower_error'):
            with self.subTest(fault=fault):
                result = self.run_native('absent', TARGET, fault)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('uncertain=0', result.stdout)
                self.assertNotIn('NATIVE_ABSENT', result.stdout)
                self.assert_child_reaped()

    def test_absent_ignores_only_historical_overflow(self):
        result = self.run_native('absent', TARGET, 'absent_old_overflow')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('NATIVE_ABSENT', result.stdout)
        self.assertIn('pre_echo_overflows=1', result.stdout)

    def test_absent_timeout_or_fresh_overflow_stays_uncertain(self):
        for fault in ('absent_no_prompt', 'absent_fresh_overflow'):
            with self.subTest(fault=fault):
                result = self.run_native('absent', TARGET, fault)
                self.assertEqual(result.returncode, 21, result.stdout)
                self.assertIn('uncertain=1', result.stdout)
                self.assertNotIn('NATIVE_ABSENT', result.stdout)
                self.assert_child_reaped()

    def test_silent_copy_failure_is_never_labelled_verified(self):
        result = self.run_native(fault='silent_copy_failure')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('copy_verified=0', result.stdout)
        self.assertFalse((self.base / 'input.jpg').exists())

    def test_invalid_paths_are_rejected_before_starting_monitor(self):
        paths = [
            ('read', 'A:/DCIM/../A6409686.JPG'),
            ('read', 'A:/DCIM/10061115/A6409686.JPG;reboot'),
            ('read', 'A:/DCIM/10061115/A6409686.jpg'),
            ('read', 'A:/DCIM/10061115/../A6409686.JPG'),
            ('read', TARGET), ('readback', CAPTURE),
            ('absent', CAPTURE), ('absent', 'A:/A6400/TEST/F12345a7.JPG'),
            ('write', CAPTURE), ('flush', CAPTURE),
            ('write', 'A:/A6400/TEST/F12345678.JPG'),
            ('write', 'A:/A6400/TEST/F12345a7.JPG'),
            ('write', 'A:/A6400/TEST/../F1234567.JPG'),
            ('anything', TARGET),
        ]
        for operation, path in paths:
            with self.subTest(operation=operation, path=path):
                result = self.run_native(operation, path)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('uncertain=0', result.stdout)
                self.assertFalse((self.base / 'monitor.pid').exists())

    def test_monitor_changed_content_or_length_is_not_executed(self):
        original = self.monitor.read_bytes()
        for data in (original + b'\n', original.replace(b'old error', b'bad error')):
            with self.subTest(bytes=len(data)):
                self.monitor.write_bytes(data)
                result = self.run_native()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('reason=monitor_pin', result.stdout)
                self.assertFalse((self.base / 'monitor.pid').exists())

    def test_signal_after_send_remains_uncertain_and_reaps_monitor(self):
        env = dict(os.environ, FILM_NATIVE_FIXTURE=str(self.base), FILM_NATIVE_FAULT='no_prompt')
        process = subprocess.Popen([str(self.binary), 'read', CAPTURE], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 2
            while not (self.base / 'command').exists() and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue((self.base / 'command').exists())
            process.send_signal(signal.SIGTERM)
            output, error = process.communicate(timeout=2)
            self.assertEqual(process.returncode, 21, output + error)
            self.assertIn('uncertain=1', output)
            self.assert_child_reaped()
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_dual_slot_b_drive_and_a7r4a_target_paths(self):
        for op, path in (
            ('read', 'B:/DCIM/10061115/A7R40001.JPG'),
            ('absent', 'B:/A7R4A/TEST/F1234567.JPG'),
            ('write', 'B:/A7R4A/TEST/F1234567.JPG'),
            ('flush', 'B:/A7R4A/TEST/F1234567.JPG'),
            ('readback', 'B:/A7R4A/TEST/F1234567.JPG'),
        ):
            with self.subTest(op=op, path=path):
                if op in ('read', 'readback', 'absent'):
                    (self.base / 'input.jpg').unlink(missing_ok=True)
                result = self.run_native(op, path, fault='delayed_copy' if op in ('read', 'readback') else 'absent_ok' if op == 'absent' else '')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for bad_op, bad_path in (
            ('read', 'C:/DCIM/10061115/A7R40001.JPG'),
            ('write', 'B:/OTHER/TEST/F1234567.JPG'),
        ):
            result = self.run_native(bad_op, bad_path)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('uncertain=0', result.stdout)

    def _build_fake_libinfrafsys(self, include_mmca=True, include_mmcb=False):
        buf = bytearray(0x80000)
        base = 0x10000
        get_letter_prefix = bytes([
            0x39, 0x49, 0x2d, 0xe9, 0xf0, 0x41, 0x05, 0x46,
            0x79, 0x44, 0x01, 0x20, 0x00, 0xaf, 0x37, 0x4c,
        ])
        buf[base + 0x18c8c:base + 0x18c8c + 16] = get_letter_prefix
        table_ptr = 0x7c000
        status_ptr = 0x7c200
        drv_a = 0x7d000
        drv_b = 0x7e000
        drv_c = 0x7f000
        struct.pack_into('<I', buf, base + 0x6e648, table_ptr)
        struct.pack_into('<I', buf, base + 0x6e694, status_ptr)
        struct.pack_into('<H', buf, status_ptr, 1)
        struct.pack_into('<I', buf, table_ptr + 0 * 4, drv_a)
        struct.pack_into('<I', buf, table_ptr + (26 + 16) * 4, drv_a)
        buf[drv_a + 0xb] = ord('A')
        buf[drv_a + 0xe1c:drv_a + 0xe1c + 15] = b'/dev/nflasha23\0'
        if include_mmca:
            struct.pack_into('<I', buf, table_ptr + 1 * 4, drv_b)
            struct.pack_into('<I', buf, table_ptr + (26 + 19) * 4, drv_b)
            buf[drv_b + 0xb] = ord('B')
            buf[drv_b + 0xe1c:drv_b + 0xe1c + 10] = b'/dev/mmca\0'
        if include_mmcb:
            struct.pack_into('<I', buf, table_ptr + 2 * 4, drv_c)
            struct.pack_into('<I', buf, table_ptr + (26 + 20) * 4, drv_c)
            buf[drv_c + 0xb] = ord('C')
            buf[drv_c + 0xe1c:drv_c + 0xe1c + 10] = b'/dev/mmcb\0'
        return buf

    def test_cold_boot_mmca_on_drive_b_rewrites_read_absent_write_flush_readback(self):
        comm = self.base / 'comm'
        maps = self.base / 'maps'
        mem = self.base / 'mem'
        comm.write_text('im.elf\n')
        maps.write_text('00010000-00080000 r-xp 00000000 b3:01 123 /usr/lib/libInfraFsys.so\n')
        mem.write_bytes(self._build_fake_libinfrafsys(include_mmca=True))
        self.header.write_text(
            self.header.read_text()
            + f'#define FILM_NATIVE_PROC_COMM "{comm}"\n'
            + f'#define FILM_NATIVE_PROC_MAPS "{maps}"\n'
            + f'#define FILM_NATIVE_PROC_MEM "{mem}"\n'
        )
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', str(self.base), str(SOURCE), '-o', str(self.binary)],
                       check=True, capture_output=True)
        b_capture = 'B' + CAPTURE[1:]
        b_target = 'B' + TARGET[1:]
        r = self.run_native('read', CAPTURE)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.base / 'command').read_text(),
                         f'IFSYS cp {b_capture} {self.base}/input.jpg')
        r = self.run_native('read', CAPTURE, fault='read_unavailable')
        self.assertEqual(r.returncode, 14, r.stdout)
        self.assertIn('reason=source_unavailable', r.stdout)
        r = self.run_native('absent', TARGET, fault='absent_ok')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('NATIVE_ABSENT', r.stdout)
        self.assertEqual((self.base / 'command').read_text(),
                         f'IFSYS cp {b_target} {self.base}/input.jpg')
        r = self.run_native('write', TARGET)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.base / 'command').read_text(),
                         f'IFSYS cp {self.base}/output.jpg {b_target}')
        r = self.run_native('flush', TARGET)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.base / 'command').read_text(), f'IFSYS flush {b_target}')
        r = self.run_native('readback', TARGET)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.base / 'command').read_text(),
                         f'IFSYS cp {b_target} {self.base}/input.jpg')
        comm.write_text('other\n')
        r = self.run_native('read', CAPTURE)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.base / 'command').read_text(),
                         f'IFSYS cp {CAPTURE} {self.base}/input.jpg')

    def test_dynamic_im_pid_proc_root_and_slot_b_mmcb_drive_lookup(self):
        proc_root = self.base / 'proc'
        (proc_root / '162').mkdir(parents=True)
        (proc_root / '999').mkdir(parents=True)
        (proc_root / '999' / 'comm').write_text('other\n')
        (proc_root / '162' / 'comm').write_text('im.elf\n')
        (proc_root / '162' / 'maps').write_text(
            '00010000-00080000 r-xp 00000000 b3:01 123 /usr/lib/libInfraFsys.so\n'
        )
        (proc_root / '162' / 'mem').write_bytes(
            self._build_fake_libinfrafsys(include_mmca=False, include_mmcb=True)
        )
        self.header.write_text(
            self.header.read_text()
            + f'#define FILM_NATIVE_PROC_ROOT "{proc_root}"\n'
        )
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', str(self.base), str(SOURCE), '-o', str(self.binary)],
                       check=True, capture_output=True)
        r = self.run_native('read', 'B:/DCIM/10061115/A7R40001.JPG')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.base / 'command').read_text(),
                         f'IFSYS cp C:/DCIM/10061115/A7R40001.JPG {self.base}/input.jpg')
        (self.base / 'monitor.pid').unlink()
        r_a = self.run_native('read', CAPTURE)
        self.assertEqual(r_a.returncode, 14, r_a.stdout)
        self.assertIn('reason=source_unavailable', r_a.stdout)
        self.assertFalse((self.base / 'monitor.pid').exists())

    def test_unmounted_mmca_in_im_never_falls_back_to_drive_a_or_runs_monitor(self):
        comm = self.base / 'comm'
        maps = self.base / 'maps'
        mem = self.base / 'mem'
        comm.write_text('im.elf\n')
        maps.write_text('00010000-00080000 r-xp 00000000 b3:01 123 /usr/lib/libInfraFsys.so\n')
        mem.write_bytes(self._build_fake_libinfrafsys(include_mmca=False))
        self.header.write_text(
            self.header.read_text()
            + f'#define FILM_NATIVE_PROC_COMM "{comm}"\n'
            + f'#define FILM_NATIVE_PROC_MAPS "{maps}"\n'
            + f'#define FILM_NATIVE_PROC_MEM "{mem}"\n'
        )
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', str(self.base), str(SOURCE), '-o', str(self.binary)],
                       check=True, capture_output=True)
        r = self.run_native('read', CAPTURE)
        self.assertEqual(r.returncode, 14, r.stdout)
        self.assertIn('reason=source_unavailable', r.stdout)
        self.assertIn('uncertain=0', r.stdout)
        self.assertNotIn('NATIVE_SENT', r.stdout)
        self.assertFalse((self.base / 'monitor.pid').exists())
        for op in ('absent', 'write', 'flush', 'readback'):
            r = self.run_native(op, TARGET)
            self.assertEqual(r.returncode, 6, r.stdout)
            self.assertIn('reason=before_send', r.stdout)
            self.assertIn('uncertain=0', r.stdout)
            self.assertNotIn('NATIVE_ABSENT', r.stdout)
            self.assertNotIn('NATIVE_SENT', r.stdout)
            self.assertFalse((self.base / 'monitor.pid').exists())

    def test_second_mwf_echo_reanchors_past_pre_exec_async_errors(self):
        result = self.run_native('read', CAPTURE, fault='reanchor_mwf_pre_exec_error')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('NATIVE_RETURN operation=read reason=fresh_prompt response_bytes=4 uncertain=0',
                      result.stdout)
        result_abs = self.run_native('absent', TARGET, fault='reanchor_absent')
        self.assertEqual(result_abs.returncode, 0, result_abs.stdout)
        self.assertIn('NATIVE_ABSENT operation=absent reason=source_open_failed',
                      result_abs.stdout)
        result_chunk = self.run_native('read', CAPTURE, fault='reanchor_prefixed_chunked')
        self.assertEqual(result_chunk.returncode, 0, result_chunk.stdout)
        self.assertIn('NATIVE_RETURN operation=read reason=fresh_prompt response_bytes=4',
                      result_chunk.stdout)

    def test_post_reanchor_error_and_between_echo_overflow_still_fail_closed(self):
        err = self.run_native('read', CAPTURE, fault='post_reanchor_error')
        self.assertEqual(err.returncode, 1, err.stdout)
        self.assertIn('reason=native_error', err.stdout)
        self.assertIn('uncertain=0', err.stdout)
        ov = self.run_native('read', CAPTURE, fault='overflow_between_echoes')
        self.assertEqual(ov.returncode, 21, ov.stdout)
        self.assertIn('reason=ring_overflow', ov.stdout)
        self.assertIn('uncertain=1', ov.stdout)
        triple = self.run_native('read', CAPTURE, fault='reanchor_triple_echo_over_cap')
        self.assertEqual(triple.returncode, 21, triple.stdout)
        self.assertIn('reason=response_capacity', triple.stdout)
        self.assertIn('uncertain=1', triple.stdout)

    def test_benign_sony_mwf_traces_do_not_break_absent_or_source_unavailable(self):
        abs_ok = self.run_native('absent', TARGET, fault='absent_mwf_benign')
        self.assertEqual(abs_ok.returncode, 0, abs_ok.stdout)
        self.assertIn('NATIVE_ABSENT operation=absent reason=source_open_failed', abs_ok.stdout)
        abs_err = self.run_native('absent', TARGET, fault='absent_mwf_error')
        self.assertEqual(abs_err.returncode, 1, abs_err.stdout)
        self.assertIn('reason=absence_unproven', abs_err.stdout)
        read_ok = self.run_native('read', CAPTURE, fault='read_unavailable_mwf_benign')
        self.assertEqual(read_ok.returncode, 14, read_ok.stdout)
        self.assertIn('reason=source_unavailable', read_ok.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
