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

    def run_wrapper(self, args, extra_env=None, path=None, cwd=None):
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
            cwd=cwd,
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

    def make_fake_proot(self):
        """A proot stand-in that records its argv (arg construction test)."""
        self.proot_log = os.path.join(self.tmp.name, "proot.log")
        fake_proot = os.path.join(self.fakebin, "proot")
        with open(fake_proot, "w") as f:
            f.write("#!/bin/bash\necho \"$@\" > %s\nexit 0\n" % self.proot_log)
        os.chmod(fake_proot, 0o755)

    def test_proot_gets_canonical_root_home_bind_and_virtual_cwd(self):
        """Termux: guest must see a virtual cwd, never the host path."""
        self.make_fake_proot()
        home = os.path.join(self.tmp.name, "home")
        work = os.path.join(self.tmp.name, "work")
        os.makedirs(home)
        os.makedirs(work)
        proc = self.run_wrapper(
            ["exec", "hello"],
            extra_env={"TERMUX_VERSION": "1", "HOME": home},
            cwd=work,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        logged = self.read_log(self.proot_log)
        self.assertIn("-r %s" % self.tmp.name, logged)
        self.assertIn("-b %s:/home" % home, logged)
        self.assertIn("--cwd=/work", logged)
        self.assertIn(self.real_bin, logged)
        self.assertNotIn("--cwd=.", logged)
        self.assertNotIn("--trust-workspace", logged)
        self.assertFalse(os.path.exists(self.native_log))

    def test_proot_recursion_guard_runs_native(self):
        """PROOT_ACTIVE=1 means already inside the guest: run directly."""
        self.make_fake_proot()
        proc = self.run_wrapper(
            ["--version"],
            extra_env={"TERMUX_VERSION": "1", "PROOT_ACTIVE": "1"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.read_log(self.native_log), "--version")
        self.assertFalse(os.path.exists(self.proot_log))

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

    def make_fake_helper(self, name):
        """A lib helper stand-in under the fake $PREFIX that logs argv."""
        log = os.path.join(self.tmp.name, name + ".log")
        helper = os.path.join(self.prefix, "lib", "muse", "muse-" + name)
        with open(helper, "w") as f:
            f.write("#!/bin/bash\necho \"$@\" > %s\nexit 0\n" % log)
        os.chmod(helper, 0o755)
        return log

    def test_import_dispatches_all_forms_to_helper(self):
        """muse import always reaches muse-import (no native verb exists)."""
        log = self.make_fake_helper("import")
        for argv in (["import"], ["import", "list"],
                     ["import", "import", "some-id", "--dry-run"],
                     ["import", "--from", "agy", "some-id"],
                     ["import", "some-id"]):
            if os.path.exists(log):
                os.remove(log)
            proc = self.run_wrapper(argv)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(os.path.exists(log), argv)
        self.assertFalse(os.path.exists(self.native_log))

    def test_stats_flag_first_form_reaches_helper(self):
        """muse stats --since X must not fall through (whitelisted flags)."""
        log = self.make_fake_helper("stats")
        proc = self.run_wrapper(["stats", "--since", "2026-10-01"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.read_log(log), "--since 2026-10-01")
        self.assertFalse(os.path.exists(self.native_log))

    def test_mcp_login_passes_through_to_native(self):
        """Native mcp subcommands (login/logout) bypass the helper."""
        log = self.make_fake_helper("mcp")
        proc = self.run_wrapper(["mcp", "login", "srv"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.read_log(self.native_log), "mcp login srv")
        self.assertFalse(os.path.exists(log))



if __name__ == "__main__":
    unittest.main()
