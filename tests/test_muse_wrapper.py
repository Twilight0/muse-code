#!/usr/bin/env python3
"""Regression tests for muse.sh launcher (run: python3 -m unittest discover -s tests).

Covers the opt-in QEMU fallback: native execution is the default even on
CPUs without AVX2, and qemu-x86_64 is used only when the user explicitly
opts in via MUSE_QEMU=1 or the --qemu first argument.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRAPPER = os.path.join(REPO_ROOT, "muse.sh")
BASH = shutil.which("bash") or "/bin/bash"


class WrapperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Fake $PREFIX/lib/muse/muse: records the args it was called with.
        self.prefix = os.path.join(self.tmp.name, "prefix")
        bindir = os.path.join(self.prefix, "lib", "muse")
        os.makedirs(bindir)
        self.real_bin = os.path.join(bindir, "muse")
        self.native_log = os.path.join(self.tmp.name, "native.log")
        with open(self.real_bin, "w") as f:
            f.write("#!/bin/bash\necho \"$@\" > %s\nexit 0\n" % self.native_log)
        os.chmod(self.real_bin, 0o755)
        # Fake qemu-x86_64 first on PATH: records the args it was called with.
        self.fakebin = os.path.join(self.tmp.name, "fakebin")
        os.makedirs(self.fakebin)
        self.qemu_log = os.path.join(self.tmp.name, "qemu.log")
        fake_qemu = os.path.join(self.fakebin, "qemu-x86_64")
        with open(fake_qemu, "w") as f:
            f.write("#!/bin/bash\necho \"$@\" > %s\nexit 0\n" % self.qemu_log)
        os.chmod(fake_qemu, 0o755)
        self.base_env = {
            "PATH": self.fakebin + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
            "PREFIX": self.prefix,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_wrapper(self, args, extra_env=None, path=None):
        env = dict(self.base_env)
        if path is not None:
            env["PATH"] = path
        env.pop("MUSE_QEMU", None)
        env.pop("TERMUX_VERSION", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [BASH, WRAPPER] + args,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )

    def read_log(self, path):
        with open(path) as f:
            return f.read().strip()

    def test_default_is_native(self):
        """No opt-in: the real binary runs directly, even without AVX2."""
        proc = self.run_wrapper(["--version"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.read_log(self.native_log), "--version")
        self.assertFalse(os.path.exists(self.qemu_log))

    def test_env_opt_in_routes_through_qemu(self):
        """MUSE_QEMU=1 routes execution via qemu-x86_64 -cpu max."""
        proc = self.run_wrapper(["--version"], extra_env={"MUSE_QEMU": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.read_log(self.qemu_log),
            "-cpu max %s --version" % self.real_bin,
        )
        self.assertFalse(os.path.exists(self.native_log))

    def test_flag_opt_in_routes_through_qemu(self):
        """--qemu as first arg routes via qemu and is stripped from $@."""
        proc = self.run_wrapper(["--qemu", "exec", "hello"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.read_log(self.qemu_log),
            "-cpu max %s exec hello" % self.real_bin,
        )
        self.assertFalse(os.path.exists(self.native_log))

    def test_opt_in_without_qemu_fails_loudly(self):
        """Opt-in with no qemu-x86_64 on PATH errors instead of running native."""
        proc = self.run_wrapper(
            ["--version"],
            extra_env={"MUSE_QEMU": "1"},
            path=self.tmp.name,  # no qemu-x86_64 here
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("qemu-x86_64 was not found", proc.stderr)
        self.assertFalse(os.path.exists(self.native_log))


if __name__ == "__main__":
    unittest.main()
