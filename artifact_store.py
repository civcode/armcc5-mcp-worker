"""Binary-safe artifact catalog with optional persistent content-addressed storage."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid


class ArtifactStore:
    MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
    MAX_CHUNK = 128 * 1024
    _FILENAME = re.compile(r"^[A-Za-z0-9_.+$-]{1,120}$")

    def _init_artifacts(self) -> None:
        self.lock = threading.Lock()
        self.artifacts: dict[str, Path] = {}
        self.artifact_metadata: dict[str, dict] = {}
        raw = os.getenv("ARMCC_ARTIFACT_DIR", "").strip()
        self.artifact_dir = Path(raw).expanduser().resolve() if raw else None
        if self.artifact_dir:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            self.index_file = self.artifact_dir / "index.json"
            if self.index_file.is_file():
                try:
                    index = json.loads(self.index_file.read_text(encoding="utf-8"))
                    if not isinstance(index, dict):
                        raise ValueError("Index is not an object")
                    for key, metadata in index.items():
                        if re.fullmatch(r"[0-9a-f]{64}", key) and isinstance(metadata, dict):
                            path = self.artifact_dir / key
                            if path.is_file():
                                self.artifacts[key] = path
                                self.artifact_metadata[key] = metadata
                except (ValueError, OSError) as ex:
                    raise ValueError(f"Invalid persistent artifact index: {ex}") from ex

    @staticmethod
    def _format(path: Path, magic: bytes) -> str:
        if magic.startswith(b"\x7fELF"):
            return "ELF"
        if magic.startswith(b"\xf7ORC"):
            return "ORC"
        if path.suffix.lower() in (".i", ".s", ".lst", ".map", ".txt", ".hex", ".json"):
            return "text"
        return "unknown"

    def _persist_index_locked(self) -> None:
        if self.artifact_dir is None:
            return
        temp = self.artifact_dir / ("index-" + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_text(json.dumps(self.artifact_metadata, sort_keys=True, indent=2), encoding="utf-8")
            os.replace(temp, self.index_file)
        finally:
            temp.unlink(missing_ok=True)

    def _register(self, path: Path) -> dict:
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        if size > self.MAX_ARTIFACT_BYTES:
            raise ValueError(f"Artifact exceeds {self.MAX_ARTIFACT_BYTES} bytes")
        with path.open("rb") as stream:
            signature = stream.read(16)
            stream.seek(0)
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        fmt = self._format(path, signature)
        artifact_id = sha if self.artifact_dir is not None else uuid.uuid4().hex
        metadata = {"artifact_id": artifact_id, "name": path.name, "size": size,
                    "sha256": sha, "format": fmt, "first_16_bytes_hex": signature.hex()}
        if signature.startswith(b"\x7fELF") and len(signature) == 16 and size >= 18:
            with path.open("rb") as stream:
                stream.seek(16)
                kind = int.from_bytes(stream.read(2), "little" if signature[5] == 1 else "big")
            metadata["elf_type"] = {1: "ET_REL", 2: "ET_EXEC", 3: "ET_DYN"}.get(kind, f"{kind}")
        with self.lock:
            if self.artifact_dir is not None:
                stored = self.artifact_dir / artifact_id
                if not stored.is_file():
                    temp = self.artifact_dir / (artifact_id + "." + uuid.uuid4().hex + ".tmp")
                    try:
                        shutil.copyfile(path, temp)
                        os.replace(temp, stored)
                    finally:
                        temp.unlink(missing_ok=True)
                self.artifacts[artifact_id] = stored
                # Preserve the original first name for content-identical uploads.
                self.artifact_metadata.setdefault(artifact_id, metadata)
                self._persist_index_locked()
                return dict(self.artifact_metadata[artifact_id])
            self.artifacts[artifact_id] = path
            self.artifact_metadata[artifact_id] = metadata
        return metadata

    def _lookup(self, artifact_id: str) -> Path:
        if not isinstance(artifact_id, str):
            raise ValueError("artifact_id must be a string")
        with self.lock:
            path = self.artifacts.get(artifact_id)
        if path is None or not path.is_file():
            raise ValueError(f"Unknown artifact ID: {artifact_id}")
        return path

    def upload_artifact(self, filename: str, content_base64: str, sha256: str = "") -> dict:
        if not isinstance(filename, str) or not self._FILENAME.fullmatch(filename) or filename in (".", ".."):
            return {"ok": False, "error": "filename must be a simple 1..120-character basename"}
        if not isinstance(content_base64, str) or len(content_base64) > 4 * ((self.MAX_ARTIFACT_BYTES + 2) // 3):
            return {"ok": False, "error": f"Base64 payload exceeds the {self.MAX_ARTIFACT_BYTES} byte artifact limit"}
        try:
            raw = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError):
            return {"ok": False, "error": "content_base64 is not valid Base64"}
        if len(raw) > self.MAX_ARTIFACT_BYTES:
            return {"ok": False, "error": "Decoded artifact exceeds 8 MiB"}
        checksum = hashlib.sha256(raw).hexdigest()
        if sha256 and (not re.fullmatch(r"[0-9a-fA-F]{64}", sha256) or sha256.lower() != checksum):
            return {"ok": False, "error": "sha256 does not match decoded bytes"}
        job = self.workspace / uuid.uuid4().hex
        job.mkdir()
        path = job / filename
        path.write_bytes(raw)
        return {"ok": True, "artifact": self._register(path), "uploaded": True}

    def list_artifacts(self, offset: int = 0, limit: int = 100) -> dict:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
            return {"ok": False, "error": "offset must be nonnegative and limit must be 1..500"}
        with self.lock:
            ids = sorted(self.artifact_metadata)
            entries = [dict(self.artifact_metadata[key]) for key in ids[offset:offset + limit]]
        return {"ok": True, "persistent": self.artifact_dir is not None, "total": len(ids),
                "offset": offset, "next_offset": offset + len(entries), "artifacts": entries}

    def get_artifact(self, artifact_id: str, offset: int = 0, max_bytes: int = 65536) -> dict:
        if type(max_bytes) is not int or not 1 <= max_bytes <= self.MAX_CHUNK:
            return {"ok": False, "error": f"max_bytes must be 1..{self.MAX_CHUNK}"}
        path = self._lookup(artifact_id)
        size = path.stat().st_size
        if type(offset) is not int or not 0 <= offset <= size:
            return {"ok": False, "error": f"offset must be 0..{size}"}
        with path.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read(max_bytes)
        with self.lock:
            meta = dict(self.artifact_metadata[artifact_id])
        return {"ok": True, "artifact_id": artifact_id, "filename": meta["name"],
                "total_bytes": size, "format": meta["format"], "sha256": meta["sha256"],
                "offset": offset, "next_offset": offset + len(chunk),
                "complete": offset + len(chunk) == size,
                "base64": base64.b64encode(chunk).decode("ascii")}
