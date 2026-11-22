import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
LIBS_112 = Path(os.environ.get("A7R4A_LIBS_112", str(WORKSPACE / "libs_112")))
TOOLCHAIN_BIN = Path(os.environ.get("A7R4A_TOOLCHAIN_BIN", str(WORKSPACE / "toolchain/bin")))


def _build_host_remote_shoot(tmp: Path) -> Path:
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        raise unittest.SkipTest("No host C compiler found")
    bin_path = tmp / "a7r4a-remote-shoot"
    subprocess.run(
        [
            cc,
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-std=c11",
            str(ROOT / "camera/a7r4a_remote_shoot.c"),
            "-o",
            str(bin_path),
        ],
        check=True,
    )
    return bin_path


class TestA7R4ARemoteShoot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_obj = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp_obj.name)
        cls.cli = _build_host_remote_shoot(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls._tmp_obj.cleanup()

    def _run(self, args, env=None, check=True):
        return subprocess.run(
            [str(self.cli)] + args,
            env=env,
            text=True,
            capture_output=True,
            check=check,
        )

    def _make_mock_runner(self, log_path: Path) -> Path:
        runner = self.tmp / f"mock_sndcmd_{log_path.stem}.sh"
        runner.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{log_path}'\n"
            "exit 0\n",
            encoding="utf-8",
        )
        runner.chmod(0o755)
        return runner

    def test_dry_run_modes(self):
        r1 = self._run(["shutter", "--bulb-ms", "45000", "--dry-run"])
        self.assertIn("DRY_RUN /usr/bin/sndcmd.elf --sync 0x007A018C d:2 d:0x1A00000D d:0 d:1 d:0", r1.stdout)
        self.assertIn("DRY_RUN /usr/bin/sndcmd.elf --sync 0x007A018C d:2 d:0x1A00000E d:0 d:1 d:0", r1.stdout)
        self.assertIn("DRY_RUN /usr/bin/sndcmd.elf --sync 0x007A018C d:2 d:0x1A00000E d:0 d:0 d:0", r1.stdout)
        self.assertIn("DRY_RUN /usr/bin/sndcmd.elf --sync 0x007A018C d:2 d:0x1A00000D d:0 d:0 d:0", r1.stdout)
        self.assertIn("REMOTE_SHUTTER_DONE bulb_ms=45000", r1.stdout)

        r2 = self._run(["interval", "--count", "3", "--interval-ms", "500", "--dry-run"])
        self.assertEqual(r2.stdout.count("REMOTE_SHOT "), 3)
        self.assertIn("REMOTE_INTERVAL_DONE count=3 interval_ms=500", r2.stdout)

        r3 = self._run(["focus-bracket", "--count", "4", "--step", "3", "--dry-run"])
        self.assertEqual(r3.stdout.count("REMOTE_SHOT "), 4)
        self.assertEqual(r3.stdout.count("d:0x1A000025 d:0 d:1 d:0x3"), 3)
        self.assertEqual(r3.stdout.count("d:0x1A000025 d:0 d:0 d:0"), 3)
        self.assertIn("REMOTE_FOCUS_BRACKET_DONE count=4 step=3", r3.stdout)

        r4 = self._run(["focus-bracket", "--count", "2", "--step", "-5", "--dry-run"])
        self.assertIn("d:0x1A000025 d:0 d:1 d:0xfffffffb", r4.stdout)

    def test_argument_validation(self):
        bad_cases = [
            [],
            ["unknown"],
            ["shutter", "--count", "2"],
            ["interval", "--count", "0", "--interval-ms", "500"],
            ["interval", "--count", "2", "--interval-ms", "50"],
            ["focus-bracket", "--count", "3", "--step", "0"],
            ["focus-bracket", "--count", "3", "--step", "8"],
            ["focus-bracket", "--count", "3", "--step", "-8"],
            ["focus-bracket", "--count", "3"],
        ]
        for args in bad_cases:
            res = self._run(args, check=False)
            self.assertNotEqual(res.returncode, 0, f"expected failure for {args}")

    def test_mock_runner_exec_and_sigint_bulb_release(self):
        log_fb = self.tmp / "fb.log"
        runner_fb = self._make_mock_runner(log_fb)
        env_fb = dict(os.environ, A7R4A_SNDCMD_BIN=str(runner_fb))

        res = self._run(
            ["focus-bracket", "--count", "2", "--step", "-2", "--settle-ms", "60", "--s1-hold-ms", "15"],
            env=env_fb,
        )
        self.assertIn("REMOTE_FOCUS_BRACKET_DONE count=2 step=-2", res.stdout)
        calls = log_fb.read_text(encoding="utf-8").splitlines()
        # Shot 1 (4) + Focus drive down/up (2) + Shot 2 (4) = 10 calls.
        self.assertEqual(len(calls), 10, calls)
        self.assertIn("d:0x1A000025 d:0 d:1 d:0xfffffffe", calls[4])
        self.assertIn("d:0x1A000025 d:0 d:0 d:0", calls[5])

        # Verify SIGINT during long bulb hold forces immediate S2 + S1 release.
        log_sig = self.tmp / "sig.log"
        runner_sig = self._make_mock_runner(log_sig)
        env_sig = dict(os.environ, A7R4A_SNDCMD_BIN=str(runner_sig))

        t0 = time.monotonic()
        proc = subprocess.Popen(
            [str(self.cli), "shutter", "--bulb-ms", "5000", "--s1-hold-ms", "15"],
            env=env_sig,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if log_sig.is_file() and "d:0x1A00000E d:0 d:1 d:0" in log_sig.read_text(encoding="utf-8"):
                    break
                time.sleep(0.01)
            self.assertTrue(log_sig.is_file(), "mock runner was never invoked")
            self.assertIn("d:0x1A00000E d:0 d:1 d:0", log_sig.read_text(encoding="utf-8"))
            proc.send_signal(signal.SIGINT)
            out, err = proc.communicate(timeout=3.0)
        finally:
            if proc.poll() is None:
                proc.kill()
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 2.0, "bulb hold did not abort promptly on SIGINT")
        self.assertEqual(proc.returncode, 128 + signal.SIGINT, err)
        self.assertIn("REMOTE_ABORT sig=2 s2_released=1 s1_released=1", err)
        sig_calls = log_sig.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(sig_calls), 4, sig_calls)
        self.assertIn("d:0x1A00000E d:0 d:0 d:0", sig_calls[2])
        self.assertIn("d:0x1A00000D d:0 d:0 d:0", sig_calls[3])

    def test_arm_cross_build_if_toolchain_present(self):
        gcc = TOOLCHAIN_BIN / "arm-linux-gnueabihf-gcc"
        if not (gcc.is_file() and LIBS_112.is_dir()):
            self.skipTest("ARM cross-toolchain or libs_112 not present")
        out_dir = self.tmp / "arm_remote"
        env = dict(os.environ, PATH=f"{TOOLCHAIN_BIN}:{os.environ.get('PATH', '')}")
        res = subprocess.run(
            ["sh", str(ROOT / "host-tests/build_a7r4a_remote_shoot.sh"), str(out_dir), str(LIBS_112)],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("BUILD_ONLY: remote shoot CLI", res.stdout)
        elf = out_dir / "a7r4a-remote-shoot.elf"
        self.assertTrue(elf.is_file())
        self.assertTrue(elf.read_bytes().startswith(b"\x7fELF"))
        self.assertIn(b"/usr/bin/sndcmd.elf", elf.read_bytes())


if __name__ == "__main__":
    unittest.main()
