"""ARMCC 5 command runner. No MCP dependency; unit-testable without Windows/Keil."""
from __future__ import annotations

import atexit
import base64
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import uuid


class ARMCCWorker:
    """Small, session-local artifact store and compiler wrapper."""

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
        self.artifacts: dict[str, Path] = {}
        self.lock = threading.Lock()
        atexit.register(shutil.rmtree, self.workspace, ignore_errors=True)

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

    def _register(self, path: Path) -> dict:
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        if size > self.MAX_ARTIFACT_BYTES:
            raise ValueError(f"Artifact exceeds {self.MAX_ARTIFACT_BYTES} bytes")
        artifact_id = uuid.uuid4().hex
        with path.open("rb") as f:
            sha256 = hashlib.file_digest(f, "sha256").hexdigest()
        with self.lock:
            self.artifacts[artifact_id] = path
        return {
            "artifact_id": artifact_id,
            "name": path.name,
            "size": size,
            "sha256": sha256,
        }

    def _lookup(self, artifact_id: str) -> Path:
        with self.lock:
            path = self.artifacts.get(artifact_id)
        if path is None or not path.is_file():
            raise ValueError(f"Unknown artifact ID: {artifact_id}")
        return path

    def compiler_info(self) -> dict:
        tools = {n: str(self._exe(n)) for n in ("armcc", "fromelf", "armlink")}
        available = {n: Path(p).is_file() for n, p in tools.items()}
        result = {"compiler_root": str(self.root), "tools": tools, "available": available}
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

    def inspect_object(self, artifact_id: str, view: str = "all") -> dict:
        """Inspect an object or linked ELF/AXF with ARM fromelf."""
        views = {
            "code": ["-c"], "symbols": ["-s"],
            "relocations": ["-r"], "sections": ["-v"],
            "all": ["-c", "-s", "-r"],
        }
        if view not in views:
            return {"ok": False, "error": f"view must be one of {list(views)}"}
        path = self._lookup(artifact_id)
        result = self._run([str(self._exe("fromelf")), "--text", *views[view], str(path)], timeout=30)
        result["view"] = view
        return result

    def link_probe(self, object_ids: list[str], entry_symbol: str = "main") -> dict:
        """Small ARMCC/armlink experiment; constrained by the installed license."""
        if not 1 <= len(object_ids) <= 12:
            return {"ok": False, "error": "Need 1..12 object IDs"}
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", entry_symbol):
            return {"ok": False, "error": "Invalid entry symbol"}
        objects = [self._lookup(i) for i in object_ids]
        job = self.workspace / uuid.uuid4().hex
        job.mkdir()
        target = job / "probe.axf"
        argv = [str(self._exe("armlink")), "--cpu", "Cortex-M3", "--entry", entry_symbol,
                "--ro-base", "0x08000000", "--rw-base", "0x20000000",
                "--output", str(target), *[str(o) for o in objects]]
        result = self._run(argv, cwd=job, timeout=30)
        result["command"] = argv
        if result["ok"] and target.is_file():
            result["axf"] = self._register(target)
        elif result["ok"]:
            result["ok"] = False
            result["error"] = "Linker returned success without producing an AXF"
        return result

    def get_artifact(self, artifact_id: str, offset: int = 0, max_bytes: int = 65536) -> dict:
        """Read a generated binary in base64 chunks for transfer to Oberon."""
        if not 0 <= max_bytes <= self.MAX_CHUNK or max_bytes == 0:
            return {"ok": False, "error": f"max_bytes must be 1..{self.MAX_CHUNK}"}
        path = self._lookup(artifact_id)
        size = path.stat().st_size
        if not 0 <= offset <= size:
            return {"ok": False, "error": f"offset must be 0..{size}"}
        with path.open("rb") as f:
            f.seek(offset)
            chunk = f.read(max_bytes)
        return {
            "ok": True, "artifact_id": artifact_id,
            "filename": path.name, "total_bytes": size,
            "offset": offset, "next_offset": offset + len(chunk),
            "complete": offset + len(chunk) == size,
            "base64": base64.b64encode(chunk).decode("ascii"),
        }
