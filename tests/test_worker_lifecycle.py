"""Offline Ctrl+C and graceful cleanup tests; no compiler binaries required."""
from __future__ import annotations

import contextlib
import io
import os
import select
import signal
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import Mock

from worker_lifecycle import run_with_cleanup


class LifecycleTests(unittest.TestCase):
    def test_normal_return_closes_once(self):
        run = Mock()
        close = Mock()
        with contextlib.redirect_stderr(io.StringIO()):
            run_with_cleanup(run, close)
        run.assert_called_once_with()
        close.assert_called_once_with()

    def test_keyboard_interrupt_exits_without_traceback(self):
        run = Mock(side_effect=KeyboardInterrupt)
        close = Mock()
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            run_with_cleanup(run, close)
        close.assert_called_once_with()
        self.assertIn("ARMCC5 MCP worker stopped.", output.getvalue())

    def test_server_exception_propagates_after_cleanup(self):
        run = Mock(side_effect=RuntimeError("server startup failed"))
        close = Mock()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "server startup failed"):
                run_with_cleanup(run, close)
        close.assert_called_once_with()

    def test_cleanup_exception_propagates(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(OSError, "locked temporary file"):
                run_with_cleanup(lambda: None, Mock(side_effect=OSError("locked temporary file")))

    def test_worker_close_is_idempotent_and_keeps_persistent_artifacts(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from worker_core import ARMCCWorker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_dir = root / "permanent"
            with mock.patch.dict("os.environ", {"ARMCC_ARTIFACT_DIR": str(artifact_dir)}):
                worker = ARMCCWorker(str(root))
            transient = worker.workspace
            self.assertTrue(transient.is_dir())
            artifact_dir.joinpath("saved.txt").write_text("keep")
            worker.close()
            worker.close()
            self.assertFalse(transient.exists())
            self.assertEqual((artifact_dir / "saved.txt").read_text(), "keep")

    @unittest.skipUnless(os.name == "posix", "SIGINT process test uses POSIX")
    def test_real_sigint_exits_cleanly(self):
        script = textwrap.dedent("""
            import time
            from worker_lifecycle import run_with_cleanup

            def run_server():
                print('READY', flush=True)
                while True:
                    time.sleep(0.05)

            def close_worker():
                print('CLEANED', flush=True)

            run_with_cleanup(run_server, close_worker)
        """)
        proc = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        try:
            ready, _, _ = select.select([proc.stdout], [], [], 5)
            self.assertTrue(ready, "Child process did not start")
            self.assertEqual(proc.stdout.readline().strip(), "READY")
            proc.send_signal(signal.SIGINT)
            stdout, stderr = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertIn("CLEANED", stdout)
            self.assertIn("ARMCC5 MCP worker stopped.", stderr)
            self.assertNotIn("Traceback", stderr)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
