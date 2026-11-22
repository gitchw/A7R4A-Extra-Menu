"""Host tests for A7R4A freestanding cold-boot preload constructor logic."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent
SRC = ROOT / 'camera/a7r4a_boot_preload.c'


class BootPreloadTests(unittest.TestCase):
    def build_host_so(self, d: Path, comm_path: Path, exe_path: Path, target_exe: str,
                      disable1: Path, disable2: Path, lock_path: Path, sh_lock_path: Path,
                      script_path: Path, shell_path: str):
        so = d / 'boot.so'
        subprocess.run([
            'cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-shared',
            '-DA7R4A_PRELOAD_HOST_TEST=1',
            f'-DA7R4A_BOOT_COMM_PATH="{comm_path}"',
            f'-DA7R4A_BOOT_EXE_PATH="{exe_path}"',
            f'-DA7R4A_BOOT_TARGET_EXE="{target_exe}"',
            f'-DA7R4A_BOOT_DISABLE_1="{disable1}"',
            f'-DA7R4A_BOOT_DISABLE_2="{disable2}"',
            f'-DA7R4A_BOOT_LOCK="{lock_path}"',
            f'-DA7R4A_BOOT_SH_LOCK="{sh_lock_path}"',
            f'-DA7R4A_BOOT_SCRIPT="{script_path}"',
            f'-DA7R4A_BOOT_SHELL="{shell_path}"',
            str(SRC), '-o', str(so),
        ], check=True, capture_output=True, text=True)
        return so

    def build_runner(self, d: Path):
        bin_path = d / 'runner'
        c = d / 'runner.c'
        c.write_text(
            '#define _POSIX_C_SOURCE 200809L\n'
            '#include <dlfcn.h>\n'
            '#include <fcntl.h>\n'
            '#include <signal.h>\n'
            '#include <stdio.h>\n'
            '#include <unistd.h>\n'
            'int main(int argc, char **argv) {\n'
            '  (void)argc;\n'
            '  sigset_t mask, cur;\n'
            '  sigemptyset(&mask);\n'
            '  sigaddset(&mask, SIGUSR1);\n'
            '  if (sigprocmask(SIG_BLOCK, &mask, NULL) != 0) return 3;\n'
            '  int fd = open("/dev/null", O_RDWR);\n'
            '  if (fd < 0) return 4;\n'
            '  if (dup2(fd, 42) < 0 || dup2(fd, 255) < 0) return 5;\n'
            '  if (fd != 42 && fd != 255) close(fd);\n'
            '  void *h = dlopen(argv[1], RTLD_NOW);\n'
            '  if (!h) { fprintf(stderr, "%s\\n", dlerror()); return 2; }\n'
            '  if (fcntl(42, F_GETFD) < 0 || fcntl(255, F_GETFD) < 0) { fprintf(stderr, "PARENT_FD_CLOSED\\n"); return 6; }\n'
            '  if (sigprocmask(SIG_SETMASK, NULL, &cur) != 0 || !sigismember(&cur, SIGUSR1)) { fprintf(stderr, "PARENT_SIG_UNBLOCKED\\n"); return 7; }\n'
            '  puts("RUNNER_OK");\n'
            '  return 0;\n'
            '}\n'
        )
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(bin_path)],
                       check=True, capture_output=True, text=True)
        return bin_path

    def test_gates_context_reset_and_dual_locks(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            comm = d / 'comm'
            exe = d / 'exe'
            dis1 = d / 'disable1'
            dis2 = d / 'disable2'
            lock = d / 'boot.lock'
            sh_lock = d / 'boot-sh.lock'
            script = d / 'boot.sh'
            ran = d / 'ran.log'
            script.write_text(
                '#!/bin/sh\n'
                f'if (: <&42) 2>/dev/null; then echo FD42_OPEN >> "{ran}"; else echo FD42_CLOSED >> "{ran}"; fi\n'
                f'if (: <&255) 2>/dev/null; then echo FD255_OPEN >> "{ran}"; else echo FD255_CLOSED >> "{ran}"; fi\n'
                f'trap \'echo USR1_UNBLOCKED >> "{ran}"; exit 0\' USR1\n'
                'kill -USR1 $$\n'
                f'echo USR1_BLOCKED >> "{ran}"\n'
            )
            script.chmod(0o755)
            target_exe = '/usr/bin/im.elf'
            exe.symlink_to(target_exe)

            so = self.build_host_so(d, comm, exe, target_exe, dis1, dis2, lock, sh_lock, script, '/bin/sh')
            runner = self.build_runner(d)

            # 1. Wrong comm -> no-op
            comm.write_text('ash\n')
            r = subprocess.run([str(runner), str(so)], check=True, capture_output=True, text=True)
            self.assertIn('RUNNER_OK', r.stdout)
            time.sleep(0.1)
            self.assertFalse(ran.exists())
            self.assertFalse(lock.exists())

            # 2. Matching comm + wrong exe -> no-op
            comm.write_text('im.elf\n')
            exe.unlink()
            exe.symlink_to('/tmp/fake_im.elf')
            subprocess.run([str(runner), str(so)], check=True, capture_output=True, text=True)
            time.sleep(0.1)
            self.assertFalse(ran.exists())

            # 3. Disable file present -> no-op
            exe.unlink()
            exe.symlink_to(target_exe)
            dis1.write_text('1')
            subprocess.run([str(runner), str(so)], check=True, capture_output=True, text=True)
            time.sleep(0.1)
            self.assertFalse(ran.exists())
            dis1.unlink()

            # 4. .a7r4a-boot-sh.lock directory present even without boot.lock -> no-op
            sh_lock.mkdir()
            subprocess.run([str(runner), str(so)], check=True, capture_output=True, text=True)
            time.sleep(0.1)
            self.assertFalse(ran.exists())
            self.assertFalse(lock.exists())
            sh_lock.rmdir()

            # 5. Valid launch -> creates lock, resets FDs/signals in grandchild (without
            #    mutating parent), double-forks, and runs script exactly once.
            r = subprocess.run([str(runner), str(so)], check=True, capture_output=True, text=True)
            self.assertIn('RUNNER_OK', r.stdout)
            for _ in range(50):
                if ran.is_file() and len(ran.read_text().splitlines()) >= 3:
                    break
                time.sleep(0.02)
            self.assertTrue(lock.is_file())
            self.assertEqual(ran.read_text().splitlines(), ['FD42_CLOSED', 'FD255_CLOSED', 'USR1_UNBLOCKED'])

            # 6. Second launch while boot.lock exists -> does not re-run script
            subprocess.run([str(runner), str(so)], check=True, capture_output=True, text=True)
            time.sleep(0.1)
            self.assertEqual(ran.read_text().splitlines(), ['FD42_CLOSED', 'FD255_CLOSED', 'USR1_UNBLOCKED'])

    def test_arm_freestanding_syscalls_and_zero_deps(self):
        toolchain_bin = Path(os.environ.get('A7R4A_TOOLCHAIN_BIN', str(ROOT.parent / 'toolchain/bin')))
        libs_dir = Path(os.environ.get('A7R4A_LIBS_DIR', str(ROOT.parent / 'libs_112')))
        if not (toolchain_bin / 'arm-linux-gnueabihf-gcc').is_file() or not (libs_dir / 'libc-2.11.2.so').is_file():
            self.skipTest('ARM cross-toolchain or libs_112 not present')
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'boot_out'
            res = subprocess.run(
                ['sh', str(ROOT / 'host-tests/build_a7r4a_boot_preload.sh'), str(out), str(libs_dir)],
                check=True, capture_output=True, text=True,
            )
            self.assertIn('BOOT_PRELOAD_ABI_OK freestanding zero-deps DT_INIT_ARRAY', res.stdout)
            dis = subprocess.run(
                [str(toolchain_bin / 'arm-linux-gnueabihf-objdump'), '-d', str(out / 'a7r4a-boot.so')],
                check=True, capture_output=True, text=True,
            ).stdout
            # Verify raw ARM EABI syscalls: setpriority (97=0x61), sched_setscheduler (156=0x9c), rt_sigprocmask (175=0xaf)
            self.assertIn('svc\t0x00000000', dis)
            self.assertRegex(dis, r'mov\s+r7,\s+#97\b')
            self.assertRegex(dis, r'mov\s+r7,\s+#156\b')
            self.assertRegex(dis, r'mov\s+r7,\s+#175\b')


if __name__ == '__main__':
    unittest.main(verbosity=2)
