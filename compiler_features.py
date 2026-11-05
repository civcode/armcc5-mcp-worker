"""Project compilation and toolchain capability reporting for ARM Compiler 5.

The worker invokes the real toolchain for every requested mode, including
license-limited modes, and returns its original diagnostic on failure. No
approximate conversion or substitute compiler is used.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import uuid

from structured_inspection import parse_fromelf


_LICENSE_RE = re.compile(r"\b([A-Z]\d{4}U):\s*Option\s+['\"].+?['\"]\s+not available with your license", re.I)
_WINDOWS_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I)


def _project_path(value: str, *, allow_root: bool = False) -> str:
    """Validate Windows-compatible paths relative to an isolated project root."""
    if allow_root and value == ".":
        return value
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ValueError("Project path must be a nonempty relative path (at most 240 characters)")
    if any(ch in value for ch in '\\:<>"|?*\x00\r\n') or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"Unsafe project path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in (".", "..") for part in value.split("/")):
        raise ValueError(f"Project paths must stay inside the job directory: {value!r}")
    if any(not part or part.endswith((".", " ")) or _WINDOWS_RESERVED.match(part)
           for part in value.split("/")):
        raise ValueError(f"Invalid Windows project filename: {value!r}")
    return path.as_posix()


def _license_diagnostic(result: dict, *, alternative: str) -> dict:
    """Distinguish the observed MDK-Lite restriction from other tool errors."""
    diagnostics = result.get("stderr", "") + "\n" + result.get("stdout", "")
    match = _LICENSE_RE.search(diagnostics)
    if match:
        result.update({
            "available": False,
            "unavailable_reason": "license",
            "diagnostic_code": match.group(1).upper(),
            "explanation": "ARM Compiler 5 rejected this option under the installed MDK-ARM Lite license. "
                           "This is a compiler/license restriction, not a missing MCP feature.",
            "alternative": alternative,
        })
    return result


class CompilerFeatures:
    """Optional capabilities mixed into ARMCCWorker without changing old tools."""

    MAX_PROJECT_FILES = 128
    MAX_PROJECT_BYTES = 4 * 1024 * 1024
    MAX_PROJECT_FILE_BYTES = 1024 * 1024
    MAX_PROJECT_SOURCES = 24

    def compiler_capabilities(self) -> dict:
        """Report observations from the user's actual 2026-11-05 license probe.

        These are *known-host observations*, not hardcoded rejections: requesting
        an unavailable mode still calls the toolchain to capture its diagnostics.
        """
        return {
            "observed_host": "x1-carbon, Windows, 2026-11-05",
            "observed_toolchain": "ARM Compiler 5.06 update 6 (build 750), MDK-ARM Lite 5.43",
            "note": "Results reflect the measured Lite installation; commands are re-tested on request "
                    "and may succeed if the installed license changes.",
            "capabilities": {
                "compile_c": {"status": "available"},
                "compile_files": {"status": "available", "notes": "Staged source/header tree using -I"},
                "preprocess": {"status": "available", "command": "armcc -E"},
                "source_disassembly": {"status": "available_with_debug_info", "command": "fromelf --text -c --interleave=source", "requires": "ARMCC -g and source-line information"},
                "structured_inspection": {"status": "available", "method": "verified fromelf-text parser; check complete/parse_warnings"},
                "assembly_listing": {
                    "status": "blocked_by_license", "command": "armcc -S --interleave",
                    "diagnostic_code": "C3921U", "alternative": "inspect_object(view='source')",
                },
                "relocatable_elf_export": {
                    "status": "blocked_by_license", "command": "fromelf --elf",
                    "diagnostic_code": "Q3921U", "alternative": "get_artifact returns original ORC bytes; use fromelf inspection",
                },
                "linker_map": {"status": "available", "command": "armlink --map --xref --symbols --info=unused --list=..."},
                "asm": {"status": "untested_on_live_host", "command": "armasm --cpu Cortex-M3 --thumb"},
                "artifact_upload": {"status": "available", "notes": "Native ORC/ELF bytes, max 8 MiB per upload"},
                "artifact_persistence": {"status": "available_if_configured", "env": "ARMCC_ARTIFACT_DIR"},
                "linker_partial_and_reloc": {"status": "untested_on_live_host", "notes": "Try --partial or --reloc separately; not equivalent"},
                "image_conversion": {"status": "untested_on_live_host", "command": "fromelf --i32combined"},
                "linked_axf_elf": {"status": "available", "notes": "Linked AXF is ELF; not equivalent to an unlinked .o"},
            },
        }

    @staticmethod
    def _validated_flags(flags: list[str] | None, *, max_count: int = 32) -> list[str]:
        if flags is None:
            return []
        if (not isinstance(flags, list) or len(flags) > max_count or
                any(not isinstance(x, str) or not 1 <= len(x) <= 240 or
                    any(ch in x for ch in ("\x00", "\r", "\n")) for x in flags)):
            raise ValueError(f"flags must be a list of at most {max_count} strings (1..240 characters each)")
        return flags

    def _run_to_file(self, arguments: list[str], *, cwd: Path, output: Path, timeout: int = 60) -> dict:
        """Capture complete compiler/fromelf stdout directly to an artifact file."""
        try:
            with output.open("wb") as stream:
                process = subprocess.run(
                    arguments, cwd=cwd, env=self._env(), stdout=stream,
                    stderr=subprocess.PIPE, timeout=timeout, check=False, shell=False,
                )
            with output.open("rb") as preview_stream:
                preview = preview_stream.read(self.MAX_REPORT_CHARS + 1).decode("utf-8", errors="replace")
            stderr = process.stderr.decode("utf-8", errors="replace")
            return {
                "ok": process.returncode == 0, "exit_code": process.returncode,
                "stdout": preview[:self.MAX_REPORT_CHARS], "stderr": stderr[:self.MAX_REPORT_CHARS],
                "stdout_truncated": output.stat().st_size > self.MAX_REPORT_CHARS,
                "stderr_truncated": len(stderr) > self.MAX_REPORT_CHARS,
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "exit_code": None, "stdout": "", "stderr": str(exc),
                    "stdout_truncated": False, "stderr_truncated": False}

    def compile_files(
        self, files: list[dict], sources: list[str] | None = None,
        include_paths: list[str] | None = None, optimization: int = 2,
        debug: bool = True, extra_flags: list[str] | None = None,
        mode: str = "object", raw_compiler_flags: list[str] | None = None,
    ) -> dict:
        """Compile a staged source tree as independent translation units.

        mode='object' emits native .o, 'preprocess' emits full .i, and 'assembly'
        tries armcc -S; Lite returns the actual C3921U license diagnostic.
        """
        try:
            if mode not in ("object", "preprocess", "assembly"):
                raise ValueError("mode must be 'object', 'preprocess', or 'assembly'")
            if type(optimization) is not int or optimization not in range(4):
                raise ValueError("optimization must be an integer from 0 to 3")
            if type(debug) is not bool:
                raise ValueError("debug must be boolean")
            flags = self._validated_flags(extra_flags, max_count=32)
            raw_flags = self._validated_flags(raw_compiler_flags, max_count=64) if raw_compiler_flags is not None else None
            if raw_flags is not None and flags:
                raise ValueError("raw_compiler_flags and extra_flags are mutually exclusive")
            if raw_flags is not None and any(x.lower() in ("-c", "-e", "-s", "-o", "--output") or
                                             x.lower().startswith("--output=") for x in raw_flags):
                raise ValueError("raw_compiler_flags must not override worker-owned mode/output")
            if not isinstance(files, list) or not 1 <= len(files) <= self.MAX_PROJECT_FILES:
                raise ValueError(f"files must contain 1..{self.MAX_PROJECT_FILES} entries")
            stored: dict[str, bytes] = {}
            total = 0
            collisions: set[str] = set()
            for item in files:
                if not isinstance(item, dict) or "path" not in item or set(item) not in (
                        {"path", "content"}, {"path", "content_base64"}):
                    raise ValueError("Each file needs 'path' and exactly one of 'content' or 'content_base64'")
                name = _project_path(item["path"])
                if "content" in item:
                    if not isinstance(item["content"], str):
                        raise ValueError("content must be UTF-8 text")
                    data = item["content"].encode("utf-8")
                else:
                    encoded = item["content_base64"]
                    if not isinstance(encoded, str) or len(encoded) > 4 * ((self.MAX_PROJECT_FILE_BYTES + 2) // 3):
                        raise ValueError("content_base64 exceeds 1 MiB decoded file limit")
                    try:
                        data = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError) as ex:
                        raise ValueError(f"Invalid content_base64 for {name}") from ex
                total += len(data)
                if len(data) > self.MAX_PROJECT_FILE_BYTES or total > self.MAX_PROJECT_BYTES:
                    raise ValueError("Project exceeds per-file 1 MiB or aggregate 4 MiB limit")
                if name.lower() in collisions:
                    raise ValueError(f"Duplicate or case-conflicting project file: {name}")
                collisions.add(name.lower())
                stored[name] = data
            if sources is None:
                chosen = [name for name in stored if name.lower().endswith(".c")]
            else:
                if not isinstance(sources, list):
                    raise ValueError("sources must be a list of staged .c file paths")
                chosen = [_project_path(s) for s in sources]
            if not 1 <= len(chosen) <= self.MAX_PROJECT_SOURCES or len({s.lower() for s in chosen}) != len(chosen):
                raise ValueError(f"Select 1..{self.MAX_PROJECT_SOURCES} distinct C sources")
            if any(name not in stored or not name.lower().endswith(".c") for name in chosen):
                raise ValueError("Each source must name a staged .c file")
            includes = include_paths if include_paths is not None else []
            if not isinstance(includes, list) or len(includes) > 32:
                raise ValueError("include_paths must contain at most 32 relative directories")
            includes = [_project_path(p, allow_root=True) for p in includes]
            known_dirs = {"."}
            for file in stored:
                parts = PurePosixPath(file).parts
                for i in range(1, len(parts)):
                    known_dirs.add("/".join(parts[:i]))
            if any(p not in known_dirs for p in includes):
                raise ValueError("include_paths must refer to existing staged directories")
        except (TypeError, UnicodeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

        job = self.workspace / uuid.uuid4().hex
        srcroot = job / "src"
        srcroot.mkdir(parents=True)
        for path, data in stored.items():
            target = srcroot.joinpath(*PurePosixPath(path).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        common = ([str(self._exe("armcc")), *raw_flags] if raw_flags is not None else
                  [str(self._exe("armcc")), "--cpu", "Cortex-M3", "--thumb", "--c99", f"-O{optimization}",
                   "-g" if debug else "--no_debug"])
        common.extend(f"-I{(srcroot / p).as_posix()}" for p in includes)
        common.extend(flags)
        results = []
        for source in chosen:
            src_path = srcroot.joinpath(*PurePosixPath(source).parts)
            suffix = {"object": ".o", "preprocess": ".i", "assembly": ".s"}[mode]
            artifact_path = job / "outputs" / PurePosixPath(source).with_suffix(suffix)
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            if mode == "preprocess":
                argv = [*common, "-E", str(src_path)]
                result = self._run_to_file(argv, cwd=srcroot, output=artifact_path, timeout=90)
            elif mode == "assembly":
                argv = [*common, "-S", "--interleave", str(src_path), "-o", str(artifact_path)]
                result = self._run(argv, cwd=srcroot, timeout=90)
                result = _license_diagnostic(result, alternative="inspect_object(view='source') provides source-interleaved disassembly")
            else:
                argv = [*common, "-c", str(src_path), "-o", str(artifact_path)]
                result = self._run(argv, cwd=srcroot, timeout=90)
            result.update({"source": source, "command": argv, "source_sha256": hashlib.sha256(stored[source]).hexdigest()})
            if result.get("ok") and artifact_path.is_file():
                try:
                    result["artifact"] = self._register(artifact_path)
                except (OSError, ValueError) as exc:
                    result.update({"ok": False, "error": str(exc)})
            elif result.get("ok"):
                result.update({"ok": False, "error": "Tool returned success without producing an output artifact"})
            results.append(result)
        return {
            "ok": all(r["ok"] for r in results), "mode": mode,
            "results": results, "include_paths": includes, "optimization": optimization,
            "debug": debug, "extra_flags": flags, "raw_compiler_flags": raw_flags,
            "effective_flags": common[1:], "files_staged": len(stored),
            "source_sha256": {name: hashlib.sha256(stored[name]).hexdigest() for name in chosen},
        }

    def export_elf(self, artifact_id: str) -> dict:
        """Try native fromelf ELF export; expose MDK-Lite Q3921U without guessing."""
        path = self._lookup(artifact_id)
        with path.open("rb") as source_stream:
            is_elf = source_stream.read(4) == b"\x7fELF"
        if is_elf:
            return {"ok": True, "already_elf": True,
                    "explanation": "Artifact already contains native ELF bytes.", "elf": self._register(path)}
        job = self.workspace / uuid.uuid4().hex
        job.mkdir()
        output = job / (path.stem + ".elf")
        argv = [str(self._exe("fromelf")), "--elf", f"--output={output}", str(path)]
        result = self._run(argv, cwd=job, timeout=60)
        result["command"] = argv
        result = _license_diagnostic(result,
            alternative="Use inspect_object(view='source', 'symbols' or 'relocations'); get_artifact returns the unchanged native ORC object")
        if result.get("ok"):
            elf_magic = b""
            if output.is_file():
                with output.open("rb") as export_stream:
                    elf_magic = export_stream.read(4)
            if elf_magic != b"\x7fELF":
                result.update({"ok": False, "error": "fromelf succeeded but did not produce a valid ELF artifact"})
            else:
                result["elf"] = self._register(output)
        return result

    def inspect_structured(self, artifact_id: str) -> dict:
        """Parse all fromelf sections, symbols and relocations, retain exact text.

        This is a reported, versioned fromelf-text parser rather than a native
        ELF parser: callers must check 'complete' and 'parse_warnings'.
        """
        path = self._lookup(artifact_id)
        job = self.workspace / uuid.uuid4().hex
        job.mkdir()
        raw = job / "fromelf_raw.txt"
        argv = [str(self._exe("fromelf")), "--text", "-c", "-s", "-r", str(path)]
        result = self._run_to_file(argv, cwd=path.parent, output=raw, timeout=90)
        result["command"] = argv
        if not result.get("ok"):
            return result
        try:
            result["raw_report"] = self._register(raw)
            parsed = parse_fromelf(raw.read_text(encoding="utf-8", errors="replace"))
            serialized = job / "structured.json.txt"
            serialized.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
            result["structured_report"] = self._register(serialized)
            result["structured"] = parsed
        except (OSError, ValueError) as exc:
            result.update({"ok": False, "error": f"Structured inspection failed: {exc}"})
        return result
