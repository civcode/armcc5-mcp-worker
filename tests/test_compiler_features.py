"""Offline tests: no ARMCC/Keil executable or Windows installation needed."""
import base64
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from worker_core import ARMCCWorker

ORC = b"\xf7ORC" + b"binary-debug-object"
ELF = b"\x7fELF" + b"linked-axf"


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "bin").mkdir()
        for name in ("armcc", "fromelf", "armlink"):
            (root / "bin" / f"{name}.exe").touch()
        self.worker = ARMCCWorker(str(root))
        self.calls = []
        self.run_patch = mock.patch.object(self.worker, "_run", side_effect=self.fake_run)
        self.run_patch.start()
        self.file_patch = mock.patch.object(self.worker, "_run_to_file", side_effect=self.fake_capture)
        self.file_patch.start()

    def tearDown(self):
        self.file_patch.stop()
        self.run_patch.stop()
        shutil.rmtree(self.worker.workspace, ignore_errors=True)
        self.tmp.cleanup()

    def fake_run(self, argv, *, cwd=None, timeout=30):
        self.calls.append((list(argv), cwd, timeout))
        exe = Path(argv[0]).stem.lower()
        if exe == "armcc" and "--vsn" in argv:
            return self._good("ARM Compiler 5.06 update 6 (build 750)")
        if exe == "armcc" and "-S" in argv:
            return self._error("Fatal error: C3921U: Option '-S' not available with your license.\n"
                               "Fatal error: C3921U: Option '--interleave' not available with your license.")
        if exe == "armcc":
            Path(argv[argv.index("-o") + 1]).write_bytes(ORC)
            return self._good()
        if exe == "fromelf" and "--elf" in argv:
            return self._error("Fatal error: Q3921U: Option '--elf' not available with your license.")
        if exe == "fromelf":
            return self._good("source line\nMOV r0, r0\n")
        if exe == "armlink":
            Path(argv[argv.index("--output") + 1]).write_bytes(ELF)
            maparg = next(a for a in argv if a.startswith("--list="))
            Path(maparg.split("=",1)[1]).write_text("Linker memory map\n", encoding="utf-8")
            return self._good()
        raise AssertionError(argv)

    def fake_capture(self, argv, *, cwd, output, timeout=60):
        self.calls.append((list(argv), cwd, timeout))
        text = ("#line 1 preprocessed input\n" + "int foo;\n" * 4500) if "-E" in argv else "disassembly\n" * 4500
        output.write_text(text, encoding="utf-8")
        return self._good(text[:32000])

    @staticmethod
    def _good(stdout=""):
        return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": "",
                "stdout_truncated": False, "stderr_truncated": False}

    @staticmethod
    def _error(stderr):
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": stderr,
                "stdout_truncated": False, "stderr_truncated": False}

    @staticmethod
    def files():
        return [{"path": "src/node.c", "content": '#include "node.h"\nint node(void) { return ANSWER; }'},
                {"path": "include/node.h", "content": "#define ANSWER 42\n"},
                {"path": "src2/node.c", "content": "int node2(void) { return 7; }"}]

    def test_multifile_separate_objects_and_inc_paths(self):
        result = self.worker.compile_files(self.files(), include_paths=["include"], optimization=3,
                                           debug=False, extra_flags=["--apcs=interwork"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["results"]), 2)
        a, b = result["results"]
        self.assertEqual(a["artifact"]["format"], "ORC")
        self.assertNotEqual(a["artifact"]["name"], "bad")
        self.assertNotEqual(self.worker._lookup(a["artifact"]["artifact_id"]),
                            self.worker._lookup(b["artifact"]["artifact_id"]))
        cmd = a["command"]
        self.assertIn("-O3", cmd)
        self.assertIn("--no_debug", cmd)
        self.assertIn("--apcs=interwork", cmd)
        self.assertTrue(any(x.startswith("-I") and x.endswith("include") for x in cmd))
        self.assertEqual(base64.b64decode(self.worker.get_artifact(a["artifact"]["artifact_id"])["base64"]), ORC)

    def test_preprocess_is_complete_downloadable_artifact(self):
        data = self.worker.compile_files(self.files(), sources=["src/node.c"],
                                         include_paths=["include"], mode="preprocess")
        self.assertTrue(data["ok"], data)
        result = data["results"][0]
        self.assertIn("-E", result["command"])
        self.assertNotIn("-c", result["command"])
        artifact = result["artifact"]
        self.assertEqual(artifact["format"], "text")
        self.assertGreater(artifact["size"], 32000)
        fetched = self.worker.get_artifact(artifact["artifact_id"], max_bytes=100)
        self.assertFalse(fetched["complete"])
        self.assertTrue(base64.b64decode(fetched["base64"]).startswith(b"#line 1"))

    def test_assembly_license_diagnostic_not_mcp_limitation(self):
        data = self.worker.compile_files(self.files(), sources=["src/node.c"], mode="assembly")
        self.assertFalse(data["ok"])
        entry = data["results"][0]
        self.assertEqual(entry["diagnostic_code"], "C3921U")
        self.assertEqual(entry["unavailable_reason"], "license")
        self.assertFalse(entry["available"])
        self.assertIn("inspect_object", entry["alternative"])
        self.assertIn("-S", entry["command"])

    def test_explicit_elf_export_reports_q3921u(self):
        obj = self.worker.compile_files(self.files(), sources=["src/node.c"])["results"][0]["artifact"]
        result = self.worker.export_elf(obj["artifact_id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["diagnostic_code"], "Q3921U")
        self.assertEqual(result["unavailable_reason"], "license")
        self.assertIn("--elf", result["command"])
        self.assertIn("ORC", result["alternative"])

    def test_already_elf_does_not_need_conversion(self):
        obj = self.worker.compile_files(self.files(), sources=["src/node.c"])["results"][0]["artifact"]
        linked = self.worker.link_probe([obj["artifact_id"]])
        self.assertTrue(linked["ok"], linked)
        self.assertEqual(linked["map"]["format"], "text")
        self.assertEqual(linked["axf"]["format"], "ELF")
        result = self.worker.export_elf(linked["axf"]["artifact_id"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["already_elf"])

    def test_scatter_file_excludes_fixed_bases(self):
        obj = self.worker.compile_files(self.files(), sources=["src/node.c"])["results"][0]["artifact"]
        data = self.worker.link_probe([obj["artifact_id"]], scatter_content="LR_ROM 0x08000000 { ER_ROM 0x08000000 { *(+RO) } }")
        self.assertTrue(data["ok"], data)
        self.assertIn("--scatter", data["command"])
        self.assertNotIn("--ro-base", data["command"])
        self.assertTrue(any(x.startswith("--list=") for x in data["command"]))
        self.assertEqual(data["map"]["format"], "text")

    def test_source_view_and_full_report(self):
        obj = self.worker.compile_files(self.files(), sources=["src/node.c"])["results"][0]["artifact"]
        output = self.worker.inspect_object(obj["artifact_id"], "source", save_output=True)
        self.assertTrue(output["ok"])
        self.assertIn("--interleave=source", output["command"])
        self.assertGreater(output["report"]["size"], 32000)

    def test_invalid_paths_are_rejected_before_execution(self):
        invalid = ["../secrets.c", "/outside.c", "C:/outside.c", "a\\other.c", "CON.c", "a//b.c", "a/../b.c"]
        for name in invalid:
            with self.subTest(path=name):
                data = self.worker.compile_files([{"path": name, "content": "int foo;"}])
                self.assertFalse(data["ok"], data)
        self.assertEqual(self.calls, [])

    def test_duplicate_casefold_and_unstaged_include(self):
        data = self.worker.compile_files([{"path":"A.c","content":"int x;"},
                                          {"path":"a.c","content":"int y;"}])
        self.assertFalse(data["ok"])
        data = self.worker.compile_files(self.files(), include_paths=["not-uploaded"])
        self.assertFalse(data["ok"])
        self.assertEqual(self.calls, [])

    def test_capability_report_distinguishes_license_from_mcp(self):
        caps = self.worker.compiler_capabilities()["capabilities"]
        self.assertEqual(caps["assembly_listing"]["diagnostic_code"], "C3921U")
        self.assertEqual(caps["relocatable_elf_export"]["diagnostic_code"], "Q3921U")
        self.assertEqual(caps["preprocess"]["status"], "available")

    def test_inspect_structured_preserves_report_and_flags_uncertainty(self):
        from test_structured_inspection import SAMPLE
        obj = self.worker.compile_files(self.files(), sources=["src/node.c"])["results"][0]["artifact"]
        def capture(argv, *, cwd, output, timeout):
            output.write_text(SAMPLE, encoding="utf-8")
            return self._good(SAMPLE)
        with mock.patch.object(self.worker, "_run_to_file", side_effect=capture):
            data = self.worker.inspect_structured(obj["artifact_id"])
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["structured"]["symbol_count"], 3)
        self.assertEqual(data["structured"]["relocation_count"], 1)
        self.assertTrue(data["structured"]["complete"])
        self.assertEqual(data["raw_report"]["format"], "text")
        self.assertEqual(data["structured_report"]["format"], "text")
        self.assertIn("-r", data["command"])

    def test_fromelf_failure_not_automatically_labeled_license(self):
        result = self.worker.compile_files(self.files(), sources=["src/node.c"])["results"][0]["artifact"]
        with mock.patch.object(self.worker, "_run", return_value=self._error("invalid ORC object")):
            data = self.worker.export_elf(result["artifact_id"])
        self.assertFalse(data["ok"])
        self.assertNotIn("unavailable_reason", data)


if __name__ == "__main__":
    unittest.main()
