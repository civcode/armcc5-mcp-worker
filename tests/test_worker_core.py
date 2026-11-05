"""Portable unit tests: simulate ARMCC tools without executing fake .exe files.

Windows cannot run a text file with a #! line as armcc.exe. All tool invocations
are therefore intercepted at the ARMCCWorker._run boundary; the actual ARMCC5
installation is not needed for these tests.
"""

import base64
import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from worker_core import ARMCCWorker


MOCK_OBJECT = b"\x7fELFfakeobject"
MOCK_AXF = b"\x7fELFfakeaxf"


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "bin").mkdir()
        # Create placeholders for compiler_info() availability checks only.
        # Never launch these files. Python mocks simulate all tool subprocesses.
        for name in ("armcc", "fromelf", "armlink", "armasm"):
            (root / "bin" / f"{name}.exe").touch()
        self.worker = ARMCCWorker(str(root))
        self.calls = []
        self.runner_patch = mock.patch.object(self.worker, "_run", side_effect=self.fake_run)
        self.runner_patch.start()

    def tearDown(self):
        self.runner_patch.stop()
        shutil.rmtree(self.worker.workspace, ignore_errors=True)
        self.tmp.cleanup()

    def fake_run(self, arguments, *, cwd=None, timeout=30):
        """Simulate armcc/fromelf/armlink results, including generated artifacts."""
        self.calls.append((list(arguments), cwd, timeout))
        name = Path(arguments[0]).stem.lower()
        if name == "armcc" and "--vsn" in arguments:
            output = "ARM Compiler 5.06 update 7 (build 960)"
        elif name == "armcc":
            Path(arguments[arguments.index("-o") + 1]).write_bytes(MOCK_OBJECT)
            output = ""
        elif name == "armlink":
            Path(arguments[arguments.index("--output") + 1]).write_bytes(MOCK_AXF)
            output = ""
        elif name == "fromelf":
            output = "disassembly / symbols"
        else:
            raise AssertionError(f"Unexpected tool: {arguments[0]}")
        return {"ok": True, "exit_code": 0, "stdout": output,
                "stderr": "", "stdout_truncated": False, "stderr_truncated": False}

    def test_info(self):
        data = self.worker.compiler_info()
        self.assertTrue(all(data["available"].values()))
        self.assertIn("960", data["armcc_version"]["stdout"])
        self.assertEqual(self.calls[0][0][-1], "--vsn")

    def test_compile_disassemble_artifact_and_link(self):
        result = self.worker.compile_c("int main(void) { return 0; }", 2)
        self.assertTrue(result["ok"], result)
        command = result["command"]
        self.assertIn("Cortex-M3", command)
        self.assertIn("-O2", command)
        self.assertIn("-g", command)
        obj = result["object"]
        self.assertEqual(obj["sha256"], hashlib.sha256(MOCK_OBJECT).hexdigest())
        self.assertEqual(base64.b64decode(self.worker.get_artifact(obj["artifact_id"])["base64"]), MOCK_OBJECT)
        self.assertIn("disassembly", self.worker.inspect_object(obj["artifact_id"])["stdout"])
        link = self.worker.link_probe([obj["artifact_id"]])
        self.assertTrue(link["ok"], link)
        self.assertEqual(self.worker.get_artifact(link["axf"]["artifact_id"])["total_bytes"], len(MOCK_AXF))

    def test_invalid_inputs(self):
        self.assertFalse(self.worker.compile_c("x", 9)["ok"])
        self.assertFalse(self.worker.compile_c("x" * (256 * 1024 + 1))["ok"])
        self.assertFalse(self.worker.link_probe([], "main")["ok"])
        self.assertFalse(self.worker.link_probe(["whatever"], "main;echo")["ok"])
        self.assertEqual(self.calls, [])

    def test_never_runs_a_fake_windows_executable(self):
        # Explicitly fail if a regression tries to invoke a real subprocess.
        with mock.patch("subprocess.run", side_effect=AssertionError("unexpected subprocess")):
            self.assertTrue(self.worker.compiler_info()["armcc_version"]["ok"])
            self.assertTrue(self.worker.compile_c("int f(void){ return 42; }")["ok"])


if __name__ == "__main__":
    unittest.main()
