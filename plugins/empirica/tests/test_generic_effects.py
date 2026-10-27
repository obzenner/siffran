#!/usr/bin/env python3
from __future__ import annotations

import signal
import tempfile
import unittest
from pathlib import Path

from adapters.sandbox import generic_effects as effects


SCRIPT = r'''#!/usr/bin/env python3
import os,sys,time
args=sys.argv[1:]
mode=args[-1] if args else "ok"
if mode == "timeout": time.sleep(10)
if mode == "stdout": os.write(1, b"x" * 70000)
if mode == "stderr": os.write(2, b"x" * 70000)
if mode == "warn": os.write(2, b"harmless warning")
if mode == "signed": os.kill(os.getpid(), 9)
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'''

# A finite margin for starting a fresh python3 on a loaded machine: a case that must finish on its own gets this long,
# and asserts that it did not time out, so a slow start fails as a timeout instead of passing for the wrong reason.
STARTUP_MARGIN_SECONDS = 30


class ConcreteEffectsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.cli = root / "docker"
        self.cli.write_text(SCRIPT)
        self.cli.chmod(0o755)
        self.config = root / "config"
        self.config.mkdir(mode=0o700)
        self.cwd = root / "cwd"
        self.cwd.mkdir()
        self.environment = {"LANG": "C", "LC_ALL": "C",
                            "DOCKER_HOST": "unix:///var/run/docker.sock",
                            "DOCKER_CONFIG": str(self.config)}

    def owner(self, **environment):
        return effects.ConcreteGenericEffects(
            self.cli, {**self.environment, **environment}, self.cwd)

    def test_successful_warning_is_bounded_and_preserves_digest(self):
        owner = self.owner()
        raw = owner.run(("container", "inspect", "warn"), timeout_seconds=STARTUP_MARGIN_SECONDS,
                        output_limit_bytes=1024)
        self.assertFalse(raw.timed_out)
        self.assertFalse(raw.output_limited)
        self.assertEqual(raw.returncode, 0)
        self.assertEqual(raw.stderr.bytes_seen, len(b"harmless warning"))
        self.assertTrue(raw.stderr.sha256.startswith("sha256:"))

    def test_timeout_kills_the_owned_group_and_is_not_output_limited(self):
        # The fake sleeps 10 s and writes nothing, so only the deadline can end it.
        raw = self.owner().run(("container", "inspect", "timeout"), timeout_seconds=0.05,
                               output_limit_bytes=1024)
        self.assertTrue(raw.timed_out)
        self.assertFalse(raw.output_limited)
        self.assertEqual(raw.returncode, -signal.SIGKILL)

    def test_each_stream_overflow_is_output_limited(self):
        # The deadline is a startup margin, not the trigger: the 70,000-byte write must be what ends the process. Timeout
        # is not accepted instead; whether the post-kill drain grace also sets timed_out is the runner's, not asserted.
        for mode, other in (("stdout", "stderr"), ("stderr", "stdout")):
            raw = self.owner().run(("container", "inspect", mode), timeout_seconds=STARTUP_MARGIN_SECONDS,
                                   output_limit_bytes=1024)
            with self.subTest(mode=mode):
                self.assertTrue(raw.output_limited)
                self.assertGreater(getattr(raw, mode).bytes_seen, 1024)
                self.assertEqual(getattr(raw, other).bytes_seen, 0)

    def test_attach_uses_independent_stdout_and_stderr_limits(self):
        owner = self.owner()
        raw = owner.attach(("container", "start", "stderr"), timeout_seconds=STARTUP_MARGIN_SECONDS,
                           stdout_limit=1024, stderr_limit=100000)
        self.assertFalse(raw.process.timed_out)
        self.assertFalse(raw.process.output_limited)
        self.assertEqual(raw.process.stderr.bytes_seen, 70000)
        self.assertTrue(raw.natural_exit)
        raw = owner.attach(("container", "start", "stderr"), timeout_seconds=STARTUP_MARGIN_SECONDS,
                           stdout_limit=100000, stderr_limit=1024)
        self.assertTrue(raw.process.output_limited)
        self.assertGreater(raw.process.stderr.bytes_seen, 1024)

    def test_attach_preserves_signed_return_and_is_never_natural(self):
        owner = self.owner()
        raw = owner.attach(("container", "start", "signed"), timeout_seconds=STARTUP_MARGIN_SECONDS,
                           stdout_limit=1024, stderr_limit=1024)
        # The fake kills itself: a timeout would also give a negative return and no natural exit, so exclude it.
        self.assertFalse(raw.process.timed_out)
        self.assertFalse(raw.process.output_limited)
        self.assertLess(raw.process.returncode, 0)
        self.assertFalse(raw.natural_exit)
        self.assertTrue(raw.forced_exit)

    def test_closed_environment_cli_and_command_vocabulary_fail_closed(self):
        with self.assertRaises(effects.GenericEffectsError):
            effects.ConcreteGenericEffects(
                self.cli, {**self.environment, "EXTRA": "x"}, self.cwd)
        self.cli.chmod(0o777)
        with self.assertRaises(effects.GenericEffectsError):
            self.owner()
        self.cli.chmod(0o755)
        marker = self.config / "credentials.json"
        marker.write_text("do-not-disclose")
        with self.assertRaises(effects.GenericEffectsError) as caught:
            self.owner()
        self.assertIn("remove foreign files", str(caught.exception))
        self.assertNotIn("do-not-disclose", str(caught.exception))
        marker.unlink()
        owner = self.owner()
        for argv in (("image", "pull", "x"), ("context", "use", "x"), ("system", "prune"),
                     ("container", "run", "x"), ("container",)):
            with self.assertRaises(effects.GenericEffectsRefused) as caught:
                owner.run(argv, timeout_seconds=1)
            self.assertIs(caught.exception.before_launch, True)
        # Docker stops flag parsing at the image (SetInterspersed(false)); guest words after the
        # subcommand are operands, never control-plane authority.
        self.assertEqual(owner._argv(("container", "rm", "pull")),
                         (str(self.cli), "container", "rm", "pull"))


if __name__ == "__main__":
    unittest.main()
