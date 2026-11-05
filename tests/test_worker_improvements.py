"""Offline high-value parity workflow tests and regression cases from the Pi agent."""
import base64
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from worker_core import ARMCCWorker
from object_comparison import _elf_section
from request_guard import checked_call


SAMPLE = '''
** Section #1 '.text' (SHT_PROGBITS) [SHF_ALLOC + SHF_EXECINSTR]
    Size   : 4 bytes (alignment 4)
    Address: 0x00000000
** Section #2 '.rel.text' (SHT_REL)
    Size   : 8 bytes (alignment 4)
    1 relocations applied to section #1 '.text'
    #      Offset    Relocation Type               Wrt Symbol                   Defined in
      0  0x00000000    2 R_ARM_ABS32               2 .data                    #3 '.data'
** Section #3 '.data' (SHT_PROGBITS) [SHF_ALLOC + SHF_WRITE]
    Size   : 4 bytes
** Section #4 '.symtab' (SHT_SYMTAB)
    Symbol table .symtab (2 symbols, 1 local)
      1  .data                      0x00000000   Lc    3  Sect  De   0x4
      2  foo                        0x00000001   Gb    1  Code  De   0x4
'''


class ImprovementsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "bin").mkdir()
        for name in ("armcc", "armlink", "armasm", "fromelf"):
            (self.root / "bin" / (name + ".exe")).touch()
        self.worker = ARMCCWorker(str(self.root))
        self.calls = []
        self.p1 = mock.patch.object(self.worker, "_run", side_effect=self.fake_run)
        self.p2 = mock.patch.object(self.worker, "_run_to_file", side_effect=self.fake_capture)
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        self.p2.stop()
        self.p1.stop()
        import shutil
        shutil.rmtree(self.worker.workspace, ignore_errors=True)
        self.tmp.cleanup()

    def fake_run(self, cmd, *, cwd=None, timeout=30):
        self.calls.append(cmd)
        tool = Path(cmd[0]).stem
        if tool in ("armcc", "armasm"):
            Path(cmd[cmd.index("-o")+1]).write_bytes(b"\xf7ORC"+b"somebytes")
        if tool == "armlink":
            dest = Path(cmd[cmd.index("--output")+1])
            dest.write_bytes(b"\x7fELF"+b"xxxx")
            for arg in cmd:
                if arg.startswith("--list="):
                    Path(arg.split("=",1)[1]).write_text("Section Cross References\nRemoving Unused input sections from the image.\nRemoving cmd_fn.o(.rev16_text)\nMemory Map of the image\n", encoding="utf-8")
        if tool == "fromelf" and any(x.startswith("--i32") for x in cmd):
            Path(next(x.split("=",1)[1] for x in cmd if x.startswith("--output="))).write_text(":00000001FF\n", encoding="ascii")
        return {"ok":True,"stdout":"disassembly without annotations", "stderr":"","exit_code":0}

    def fake_capture(self, cmd, *, cwd, output, timeout=30):
        self.calls.append(cmd)
        output.write_text(SAMPLE if "fromelf" in Path(cmd[0]).stem else "tool --help details", encoding="utf-8")
        return {"ok":True,"stdout":"", "stderr":"", "exit_code":0}

    def upload(self, name="vendor.o", data=b"\xf7ORC-actual-binary"):
        r=self.worker.upload_artifact(name,base64.b64encode(data).decode(),hashlib.sha256(data).hexdigest())
        self.assertTrue(r["ok"],r)
        return r["artifact"]["artifact_id"]

    def test_upload_retains_exact_bytes_and_hash(self):
        data=b"\xf7ORC\x00\xff\xc2\xa3"
        artifact=self.upload("vendor.o",data)
        result=self.worker.get_artifact(artifact)
        self.assertEqual(result["format"],"ORC")
        self.assertEqual(base64.b64decode(result["base64"]),data)
        self.assertEqual(result["sha256"],hashlib.sha256(data).hexdigest())

    def test_upload_rejects_bad_input(self):
        self.assertFalse(self.worker.upload_artifact("../escaped.o",base64.b64encode(b"abc").decode())["ok"])
        self.assertFalse(self.worker.upload_artifact("file.o","???")["ok"])
        self.assertFalse(self.worker.upload_artifact("file.o",base64.b64encode(b"abc").decode(),"0"*64)["ok"])

    def test_upload_size_limit_before_decode(self):
        self.assertFalse(self.worker.upload_artifact("huge.o", "A"*(4*((8*1024*1024+2)//3)+4))["ok"])

    def test_list_and_unknown_id_are_structured(self):
        self.upload()
        self.assertEqual(self.worker.list_artifacts()["total"],1)
        fake_ctx=type("C", (), {"request_context":type("R", (), {"params":{"arguments":{"artifact_id":"missing"}}})()})()
        result=checked_call(fake_ctx,"get_artifact",{"artifact_id"},self.worker.get_artifact,"missing")
        self.assertFalse(result["ok"])
        self.assertIn("Unknown artifact ID",result["error"])

    def test_full_map_requested_and_report_stored(self):
        a=self.upload()
        result=self.worker.link_probe([a],link_flags=["--strict"])
        self.assertTrue(result["ok"],result)
        self.assertTrue(result["unused_sections_reported"])
        self.assertTrue(result["cross_references_reported"])
        for sw in ("--map","--xref","--info=unused","--symbols","--strict"):
            self.assertIn(sw,result["command"])
        report=self.worker.get_artifact(result["map"]["artifact_id"])
        self.assertIn(b"Removing cmd_fn.o",base64.b64decode(report["base64"]))

    def test_partial_and_reloc_are_distinct(self):
        a=self.upload()
        part=self.worker.link_probe([a],link_flags=["--partial"])
        self.assertTrue(part["ok"])
        self.assertTrue(part["partial_link"])
        self.assertNotIn("--entry",part["command"])
        self.assertIn("--partial",part["command"])
        self.assertIn("artifact",part)
        self.assertNotIn("axf",part)
        reloc=self.worker.link_probe([a],link_flags=["--reloc"])
        self.assertTrue(reloc["ok"])
        self.assertFalse(reloc["partial_link"])
        self.assertIn("--entry",reloc["command"])
        self.assertIn("--reloc",reloc["command"])

    def test_link_flags_reject_output_override(self):
        a=self.upload()
        r=self.worker.link_probe([a],link_flags=["--output=../../escape"])
        self.assertFalse(r["ok"])
        self.assertEqual(self.calls,[])

    def test_compile_asm_invokes_armasm(self):
        result=self.worker.compile_asm("    AREA |.text|, CODE, READONLY\n    THUMB\n    END")
        self.assertTrue(result["ok"],result)
        self.assertEqual(Path(result["command"][0]).stem,"armasm")
        self.assertEqual(result["artifact"]["format"],"ORC")

    def test_asm_source_base64_preserves_non_ascii(self):
        data=b"\x80AREA .text\r\nEND\r\n"
        r=self.worker.compile_asm(content_base64=base64.b64encode(data).decode())
        self.assertTrue(r["ok"])
        self.assertEqual(Path(r["command"][-1]).read_bytes(),data)

    def test_source_interleaving_warning_no_debug(self):
        a=self.upload()
        result=self.worker.inspect_object(a,view="source")
        self.assertTrue(result["ok"])
        self.assertFalse(result["source_interleaving_detected"])
        self.assertIn("-g",result["warning"])

    def test_compare_normalized_orc_no_fake_text_equivalence(self):
        a=self.upload()
        b=self.upload("candidate.o",b"\xf7ORC-different")
        result=self.worker.compare_objects(a,b)
        self.assertTrue(result["ok"],result)
        self.assertTrue(result["symbol_diffs"]["identical"])
        self.assertTrue(result["reloc_diffs"]["identical"])
        self.assertIsNone(result["text_identical"])
        self.assertIn("ORC",result["text_warning"])

    def test_binary_safe_multifile_staging_and_exact_compiler_flags(self):
        original=b'const char *s="\xa3B";\r\n'
        r=self.worker.compile_files([{"path":"src/main.c","content_base64":base64.b64encode(original).decode()}],
                raw_compiler_flags=["--cpu=Cortex-M3","--c99","--apcs=interwork","-O3","--no_debug"])
        self.assertTrue(r["ok"],r)
        argv=r["results"][0]["command"]
        self.assertEqual(Path(argv[argv.index("-c")+1]).read_bytes(),original)
        self.assertIn("-O3",argv)
        self.assertNotIn("-O2",argv)
        self.assertNotIn("-g",argv)
        self.assertEqual(r["source_sha256"]["src/main.c"],hashlib.sha256(original).hexdigest())

    def test_help_without_object_artifact(self):
        result=self.worker.tool_help("armcc")
        self.assertTrue(result["ok"])
        self.assertIn("report",result)
        self.assertIn("--help",result["command"])

    def test_convert_image_uses_fromelf_not_armlink(self):
        a=self.upload("probe.axf",b"\x7fELF"+b"fake")
        r=self.worker.convert_image(a)
        self.assertTrue(r["ok"])
        self.assertEqual(Path(r["command"][0]).stem,"fromelf")
        self.assertIn("--i32combined",r["command"])

    def test_strict_unknown_flags_does_not_execute(self):
        ctx=type("C", (), {"request_context":type("R", (), {"params":{"arguments":{"source":"foo", "flags":["-O3"]}}})()})()
        invoked=[]
        result=checked_call(ctx,"compile_c",{"source","optimization","debug","extra_flags"},lambda *xs:invoked.append(xs),"foo")
        self.assertFalse(result["ok"])
        self.assertEqual(result["unknown_arguments"],["flags"])
        self.assertEqual(invoked,[])

    def test_mapping_proxy_raw_arguments_are_validated(self):
        from types import MappingProxyType
        ctx = type("C", (), {"request_context": type("R", (), {
            "params": MappingProxyType({"arguments": MappingProxyType({"tool": "value"})})
        })()})()
        result = checked_call(ctx, "tool_help", {"tool"}, lambda: {"ok": True})
        self.assertTrue(result["ok"], result)
        result = checked_call(ctx, "tool_help", set(), lambda: {"ok": True})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "UnknownToolArgument")

    def test_persistence_survives_new_worker(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict("os.environ",{"ARMCC_ARTIFACT_DIR":directory}):
            a=ARMCCWorker(str(self.root))
            r=a.upload_artifact("test.o",base64.b64encode(b"\xf7ORC-data").decode())
            identifier=r["artifact"]["artifact_id"]
            b=ARMCCWorker(str(self.root))
            self.assertEqual(b.get_artifact(identifier)["sha256"],r["artifact"]["sha256"])
            self.assertEqual(identifier,b.upload_artifact("second.o",base64.b64encode(b"\xf7ORC-data").decode())["artifact"]["artifact_id"])
            self.assertTrue(b.list_artifacts()["persistent"])

if __name__ == "__main__":
    unittest.main()
