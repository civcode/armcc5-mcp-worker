"""ARMCC 5 command runner. No MCP dependency; unit-testable without Windows/Keil."""
from __future__ import annotations

import atexit
import base64
import binascii
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import uuid

from compiler_features import CompilerFeatures, _license_diagnostic
from artifact_store import ArtifactStore
from object_comparison import ObjectComparison


class ARMCCWorker(CompilerFeatures, ArtifactStore, ObjectComparison):
    """ARM Compiler 5 subprocess worker and optional persistent artifact catalog."""

    MAX_SOURCE_BYTES = 256 * 1024
    MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
    MAX_CHUNK = 128 * 1024
    MAX_REPORT_CHARS = 32000

    def __init__(self, compiler_root: str | None = None) -> None:
        local = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        default_root = Path(local) / "Keil_v5" / "ARM" / "ARM_Compiler_5.06u6"
        self.root = Path(compiler_root or os.getenv("ARMCC_ROOT") or default_root)
        self.bin = self.root / "bin"
        self.workspace = Path(tempfile.mkdtemp(prefix="armcc5-worker-"))
        self._init_artifacts()
        self._close_lock = threading.Lock()
        self._closed = False
        # An interpreter-exit fallback; the server also calls close() after
        # Uvicorn drains requests on Ctrl+C/SIGTERM.
        atexit.register(self.close)

    def close(self) -> None:
        """Release transient job files; never delete ARMCC_ARTIFACT_DIR.

        Safe to call both after a graceful server shutdown and from atexit.
        """
        with self._close_lock:
            if self._closed:
                return
            shutil.rmtree(self.workspace, ignore_errors=True)
            self._closed = True

    def _exe(self, name: str) -> Path:
        return self.bin / f"{name}.exe"

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        return env

    def _run(self, arguments: list[str], *, cwd: Path | None = None, timeout: int = 30) -> dict:
        try:
            process = subprocess.run(
                arguments, cwd=cwd or self.workspace, env=self._env(),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, shell=False, check=False,
            )
            return {
                "ok": process.returncode == 0,
                "exit_code": process.returncode,
                "stdout": process.stdout[:self.MAX_REPORT_CHARS],
                "stderr": process.stderr[:self.MAX_REPORT_CHARS],
                "stdout_truncated": len(process.stdout) > self.MAX_REPORT_CHARS,
                "stderr_truncated": len(process.stderr) > self.MAX_REPORT_CHARS,
            }
        except (FileNotFoundError, PermissionError, OSError) as ex:
            return {"ok": False, "exit_code": None, "stdout": "", "stderr": str(ex)}
        except subprocess.TimeoutExpired as ex:
            return {"ok": False, "exit_code": None, "stdout": "", "stderr": f"Timed out after {timeout}s: {ex}"}

    def compiler_info(self) -> dict:
        tools = {n: str(self._exe(n)) for n in ("armcc", "fromelf", "armlink", "armasm")}
        available = {n: Path(p).is_file() for n, p in tools.items()}
        result = {"compiler_root": str(self.root), "tools": tools, "available": available,
                  "capabilities": self.compiler_capabilities()}
        if available["armcc"]:
            result["armcc_version"] = self._run([str(self._exe("armcc")), "--vsn"], timeout=10)
        return result

    def compile_c(
        self, source: str, optimization: int = 2, debug: bool = True,
        extra_flags: list[str] | None = None,
    ) -> dict:
        """Compile one C translation unit to an ARMCC object. No linking."""
        raw = source.encode("utf-8")
        if len(raw) > self.MAX_SOURCE_BYTES:
            return {"ok": False, "error": "Source exceeds 256 KiB"}
        if optimization not in range(4):
            return {"ok": False, "error": "optimization must be 0, 1, 2 or 3"}
        # Options are passed directly to armcc, never to a Windows shell.
        extra_flags = extra_flags or []
        if len(extra_flags) > 20 or any(not isinstance(x, str) or len(x) > 120 or "\x00" in x for x in extra_flags):
            return {"ok": False, "error": "Invalid extra_flags"}
        job = self.workspace / uuid.uuid4().hex
        job.mkdir()
        cfile, objfile = job / "probe.c", job / "probe.o"
        cfile.write_bytes(raw)
        argv = [str(self._exe("armcc")), "--cpu", "Cortex-M3", "--thumb", "--c99", f"-O{optimization}"]
        if debug:
            argv.append("-g")
        argv.extend(extra_flags)
        argv.extend(["-c", str(cfile), "-o", str(objfile)])
        result = self._run(argv, cwd=job, timeout=60)
        result["command"] = argv
        if result["ok"] and objfile.is_file():
            result["object"] = self._register(objfile)
        elif result["ok"]:
            result["ok"] = False
            result["error"] = "Compiler returned success without producing an object"
        return result

    def inspect_object(self, artifact_id: str, view: str = "all", save_output: bool = False) -> dict:
        """fromelf text inspection; flag missing source annotations explicitly."""
        views = {
            "code": ["-c"], "symbols": ["-s"],
            "relocations": ["-r"], "sections": ["-v"],
            "all": ["-c", "-s", "-r"],
            "source": ["-c", "--interleave=source"],
        }
        if view not in views:
            return {"ok": False, "error": f"view must be one of {list(views)}"}
        path = self._lookup(artifact_id)
        argv = [str(self._exe("fromelf")), "--text", *views[view], str(path)]
        if save_output:
            out = self.workspace / uuid.uuid4().hex
            out.mkdir()
            report = out / f"fromelf_{view}.txt"
            result = self._run_to_file(argv, cwd=path.parent, output=report, timeout=60)
            if result["ok"]:
                result["report"] = self._register(report)
        else:
            result = self._run(argv, cwd=path.parent, timeout=30)
        result.update({"view": view, "command": argv})
        if view == "source" and result.get("ok"):
            # Probe-verified source annotations start with ';;;'. A -g-less ORC
            # still disassembles successfully, but loses line correlations.
            if save_output:
                with report.open("rb") as stream:
                    detected = any(line.lstrip().startswith(b";;;") for line in stream)
            else:
                detected = any(line.lstrip().startswith(";;;") for line in
                               result.get("stdout", "").splitlines())
            result["source_interleaving_detected"] = detected
            if not detected:
                result["warning"] = ("No source-line annotations detected. Compile with debug=true (-g) "
                                     "and make corresponding source available; this output is code-only, "
                                     "not a compiler -S listing.")
        return result

    def link_probe(self, object_ids: list[str], entry_symbol: str = "main",
                   scatter_content: str | None = None, link_flags: list[str] | None = None) -> dict:
        """Link and emit full Keil-style report, or test --partial/--reloc."""
        if not isinstance(object_ids, list) or not 1 <= len(object_ids) <= 12:
            return {"ok": False, "error": "Need 1..12 object IDs"}
        if not isinstance(entry_symbol, str) or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", entry_symbol):
            return {"ok": False, "error": "Invalid entry symbol"}
        if scatter_content is not None and (not isinstance(scatter_content, str) or
                                            len(scatter_content.encode("utf-8")) > 256 * 1024):
            return {"ok": False, "error": "scatter_content must be UTF-8 text <= 256 KiB"}
        try:
            flags = self._validated_flags(link_flags, max_count=40)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if any(x.lower().split("=")[0] in ("--output", "-o", "--list", "--scatter") for x in flags):
            return {"ok": False, "error": "link_flags cannot override output, listing or scatter paths; use scatter_content"}
        if "--partial" in flags and scatter_content is not None:
            return {"ok": False, "error": "--partial cannot be combined with scatter_content"}
        objects = [self._lookup(i) for i in object_ids]
        job = self.workspace / uuid.uuid4().hex
        job.mkdir()
        partial = "--partial" in flags or "--ldpartial" in flags
        target = job / ("probe.o" if partial else "probe.axf")
        listing = job / "probe.map"
        argv = [str(self._exe("armlink")), "--cpu", "Cortex-M3"]
        if not partial:
            argv.extend(["--entry", entry_symbol])
            if scatter_content is None:
                argv.extend(["--ro-base", "0x08000000", "--rw-base", "0x20000000"])
            else:
                scatter = job / "probe.sct"
                scatter.write_text(scatter_content, encoding="utf-8")
                argv.extend(["--scatter", str(scatter)])
            argv.extend(["--map", "--xref", "--symbols", "--info=unused", f"--list={listing}"])
        argv.extend([*flags, "--output", str(target), *[str(o) for o in objects]])
        result = self._run(argv, cwd=job, timeout=90)
        result["command"] = argv
        result["link_flags"] = flags
        result["partial_link"] = partial
        result = _license_diagnostic(result, alternative="Link with supported options under the installed MDK-Lite license")
        if listing.is_file():
            result["map"] = self._register(listing)
            map_text = listing.read_text(encoding="utf-8", errors="replace")
            result["unused_sections_reported"] = ("Removing Unused" in map_text or "Removing " in map_text)
            result["cross_references_reported"] = "Section Cross References" in map_text
        if result["ok"] and target.is_file():
            output = self._register(target)
            result["artifact"] = output
            if not partial:
                result["axf"] = output
            elif output["format"] != "ELF":
                result["warning"] = "--partial completed but did not produce recognizable ELF bytes"
            elif output.get("elf_type") != "ET_REL":
                result["warning"] = "--partial output is ELF but not confirmed ET_REL; do not treat it as a relocatable object"
        elif result["ok"]:
            result["ok"] = False
            result["error"] = "Linker returned success without producing an output artifact"
        if partial:
            result["note"] = "Partial link may change symbols, sections and relocations; not a lossless ORC-to-ELF conversion"
        return result

    def compile_asm(self, source: str = "", content_base64: str = "",
                    extra_flags: list[str] | None = None) -> dict:
        """Run native ARMASM5 on a standalone ARMASM (.s) source."""
        if bool(source) == bool(content_base64):
            return {"ok": False, "error": "Supply exactly one of source or content_base64"}
        try:
            if source:
                data = source.encode("utf-8")
            else:
                data = base64.b64decode(content_base64, validate=True)
            flags = self._validated_flags(extra_flags, max_count=32)
        except (ValueError, UnicodeError, binascii.Error) as exc:
            return {"ok": False, "error": str(exc)}
        if len(data) > self.MAX_SOURCE_BYTES:
            return {"ok": False, "error": "Assembly input exceeds 256 KiB"}
        job = self.workspace / uuid.uuid4().hex
        job.mkdir()
        asm, out = job / "probe.s", job / "probe.o"
        asm.write_bytes(data)
        argv = [str(self._exe("armasm")), "--cpu", "Cortex-M3", "--thumb", *flags,
                "-o", str(out), str(asm)]
        result = self._run(argv, cwd=job, timeout=60)
        result.update({"command": argv, "source_sha256": hashlib.sha256(data).hexdigest()})
        result = _license_diagnostic(result, alternative="Use compiler-generated ORC and fromelf disassembly")
        if result["ok"] and out.is_file():
            result["object"] = self._register(out)
            result["artifact"] = result["object"]
        elif result["ok"]:
            result.update({"ok": False, "error": "Assembler produced no object"})
        return result

    def tool_help(self, tool: str = "armcc") -> dict:
        """Report authentic ARM tool --help without requiring a generated object."""
        if tool not in ("armcc", "armlink", "fromelf", "armasm"):
            return {"ok": False, "error": "tool must be armcc, armlink, fromelf, or armasm"}
        job = self.workspace / uuid.uuid4().hex
        job.mkdir()
        out = job / f"{tool}_help.txt"
        argv = [str(self._exe(tool)), "--help"]
        result = self._run_to_file(argv, cwd=job, output=out, timeout=30)
        result.update({"command": argv, "tool": tool})
        if out.is_file():
            result["report"] = self._register(out)
        return result

    def convert_image(self, artifact_id: str, format: str = "i32combined") -> dict:
        """Convert a linked AXF to Intel HEX using fromelf, not armlink."""
        if format not in ("i32", "i32combined"):
            return {"ok": False, "error": "format must be i32 or i32combined"}
        path = self._lookup(artifact_id)
        job = self.workspace / uuid.uuid4().hex
        job.mkdir()
        destination = job / "probe.hex"
        argv = [str(self._exe("fromelf")), f"--{format}", f"--output={destination}", str(path)]
        result = self._run(argv, cwd=job, timeout=90)
        result["command"] = argv
        result = _license_diagnostic(result, alternative="Use a linker image format supported by your toolchain")
        if result["ok"]:
            if destination.is_file():
                result["artifact"] = self._register(destination)
            else:
                result.update({"ok": False, "error": "fromelf returned success without an output file; check load-region format"})
        return result
