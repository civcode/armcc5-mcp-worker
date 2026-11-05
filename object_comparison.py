"""Symmetric object comparison via the same native fromelf parser on both sides.

No ORC-to-ELF deobfuscation. Byte-identical .text is reported only if the
section bytes can be read directly from valid ELF files; never inferred from
fromelf symbol/relocation counts.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import struct


def _elf_section(path, target_name: str) -> bytes | None:
    """Read uncompressed section bytes from a complete little/big ELF32/ELF64 file."""
    blob = path.read_bytes()
    if len(blob) < 64 or blob[:4] != b"\x7fELF" or blob[4] not in (1, 2) or blob[5] not in (1, 2):
        return None
    endian = "<" if blob[5] == 1 else ">"
    elf32 = blob[4] == 1
    try:
        if elf32:
            shoff = struct.unpack_from(endian + "I", blob, 32)[0]
            shentsize, shnum, strindex = struct.unpack_from(endian + "HHH", blob, 46)
            section_layout = endian + "IIIIIIIIII"
        else:
            shoff = struct.unpack_from(endian + "Q", blob, 40)[0]
            shentsize, shnum, strindex = struct.unpack_from(endian + "HHH", blob, 58)
            section_layout = endian + "IIQQQQIIQQ"
        expected = struct.calcsize(section_layout)
        if shentsize < expected or not shnum or shnum > 8192 or strindex >= shnum or shoff + shentsize * shnum > len(blob):
            return None
        rows = [struct.unpack_from(section_layout, blob, shoff + i * shentsize) for i in range(shnum)]
        # Both ELF32 and ELF64 section layouts use offset and size as fields 4,5.
        strrow = rows[strindex]
        strs = blob[strrow[4]:strrow[4] + strrow[5]]
        if len(strs) != strrow[5]:
            return None
        for row in rows:
            n = row[0]
            if n >= len(strs):
                continue
            end = strs.find(b"\0", n)
            if end < 0:
                continue
            if strs[n:end].decode("utf-8", errors="replace") == target_name:
                offset, length = row[4], row[5]
                if offset + length > len(blob) or row[1] == 8:  # SHT_NOBITS
                    return None
                return blob[offset:offset+length]
    except (struct.error, IndexError):
        return None
    return None


def _normalize_symbol(item: dict, sections: dict[int, str]) -> tuple:
    # Preserve ambiguous codes verbatim, rather than silently collapsing ABI differences.
    binding = {"Gb": "GLOBAL", "Lc": "LOCAL", "Wk": "WEAK"}.get(item["bind"], item["bind"])
    visibility = {"De": "DEFAULT", "Hi": "HIDDEN", "Pr": "PROTECTED"}.get(item["visibility"], item["visibility"])
    kind = {"Code": "FUNC", "Data": "OBJECT", "Sect": "SECTION", "File": "FILE"}.get(item["type"], item["type"])
    section = item["section"]
    if isinstance(section, str) and section.isdecimal():
        section = sections.get(int(section), "#" + section)
    elif section == "Ref":
        section = "UND"
    return (item["name"], binding, visibility, kind, section, item.get("size"), item.get("value"))


def _diff_counter(left: Counter, right: Counter, limit: int = 150) -> dict:
    left_only = sorted((left - right).elements(), key=str)
    right_only = sorted((right - left).elements(), key=str)
    return {"left_only_count": len(left_only), "right_only_count": len(right_only),
            "left_only": [list(x) for x in left_only[:limit]],
            "right_only": [list(x) for x in right_only[:limit]],
            "truncated": max(len(left_only), len(right_only)) > limit,
            "identical": not left_only and not right_only}


class ObjectComparison:
    def compare_objects(self, a: str, b: str) -> dict:
        """Compare normalized native-fromelf sections/symbols/relocations symmetrically."""
        left_path = self._lookup(a)
        right_path = self._lookup(b)
        left = self.inspect_structured(a)
        right = self.inspect_structured(b)
        if not left.get("ok") or not right.get("ok"):
            return {"ok": False, "error": "fromelf inspection failed", "left": left, "right": right}
        x, y = left["structured"], right["structured"]
        if not x["complete"] or not y["complete"]:
            return {"ok": False, "error": "Unverified fromelf parse; comparison would be unreliable",
                    "left_parse_warnings": x["parse_warnings"], "right_parse_warnings": y["parse_warnings"],
                    "left_report": left.get("raw_report"), "right_report": right.get("raw_report")}
        def as_counter(value, fn):
            return Counter(fn(item) for item in value)
        left_sections = {s["index"]: s["name"] for s in x["sections"]}
        right_sections = {s["index"]: s["name"] for s in y["sections"]}
        section_fn = lambda s: (s["name"], s.get("type"), s.get("size"), s.get("alignment"),
                                tuple(s.get("flags", [])))
        rel_fn = lambda r: (r["offset"], r["type"], r["symbol"], r.get("defined_section_name"))
        sec = _diff_counter(as_counter(x["sections"], section_fn), as_counter(y["sections"], section_fn))
        sym = _diff_counter(Counter(_normalize_symbol(s, left_sections) for s in x["symbols"]),
                            Counter(_normalize_symbol(s, right_sections) for s in y["symbols"]))
        reloc = _diff_counter(as_counter(x["relocations"], rel_fn), as_counter(y["relocations"], rel_fn))
        def sizes(parsed, sec_name):
            return sum(int(s["size"] or 0) for s in parsed["sections"] if s["name"] == sec_name)
        text_a, text_b = _elf_section(left_path, ".text"), _elf_section(right_path, ".text")
        identical = (text_a == text_b) if text_a is not None and text_b is not None else None
        result = {
            "ok": True, "method": "fromelf-text normalization for both inputs",
            "left_id": a, "right_id": b,
            "text_identical": identical,
            "text_sha256": [hashlib.sha256(z).hexdigest() if z is not None else None for z in (text_a, text_b)],
            "text_size_delta": sizes(y, ".text") - sizes(x, ".text"),
            "section_diffs": sec, "symbol_diffs": sym, "reloc_diffs": reloc,
            "data_diffs": _diff_counter(
                as_counter([s for s in x["sections"] if s["name"].startswith((".data", ".rodata"))], section_fn),
                as_counter([s for s in y["sections"] if s["name"].startswith((".data", ".rodata"))], section_fn)),
            "left_raw_report": left["raw_report"], "right_raw_report": right["raw_report"],
        }
        if identical is None:
            result["text_warning"] = "Exact .text bytes unavailable in native ORC; section sizes and relocations are comparable but text_identical is unknown"
        return result
