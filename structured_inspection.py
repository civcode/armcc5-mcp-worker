"""Best-effort structured sections/symbols/relocations from full ARM fromelf text.

The native MDK-Lite ORC object is not ELF. This parser deliberately preserves
fromelf's original output, tracks evidence counts, and reports any uncertainty
instead of claiming it reconstructed a complete ELF object.
"""
from __future__ import annotations

import re

SECTION = re.compile(r"^\*\* Section #(\d+) '([^']+)' \((SHT_[A-Z_0-9]+)\)(?: \[([^\]]+)\])?")
SIZE = re.compile(r"^\s+Size\s+: (\d+) bytes(?: \(alignment (\d+)\))?")
ADDRESS = re.compile(r"^\s+Address: (0x[0-9a-fA-F]+)")
SYMTAB = re.compile(r"^\s+Symbol table \S+ \((\d+) symbols, (\d+) local\)")
SYMBOL = re.compile(
    r"^\s*(\d+)\s+(.+?)\s+(0x[0-9a-fA-F]+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(0x[0-9a-fA-F]+))?\s*$"
)
SYMBOL_HEAD = re.compile(r"^\s*(\d+)\s+(\S[^\n]*?)\s*$")
SYMBOL_TAIL = re.compile(r"^\s*(0x[0-9a-fA-F]+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(0x[0-9a-fA-F]+))?\s*$")
RELOC_SUMMARY = re.compile(r"^\s+(\d+) relocations? applied to section #(\d+) '([^']+)'$")
RELOC = re.compile(r"^\s*(\d+)\s+(0x[0-9a-fA-F]+)\s+(\d+)\s+(R_[A-Z0-9_]+)\s+(\d+)\s+(.+?)\s+#(\d+) '([^']+)'\s*$")
RELOC_UNDEFINED = re.compile(r"^\s*(\d+)\s+(0x[0-9a-fA-F]+)\s+(\d+)\s+(R_[A-Z0-9_]+)\s+(\d+)\s+(\S+)(?:\s+(.+))?$")


def parse_fromelf(text: str) -> dict:
    sections: list[dict] = []
    symbols: list[dict] = []
    relocations: list[dict] = []
    warnings: list[str] = []
    current = None
    symbol_expected = None
    reloc_expected: dict[int, int] = {}
    pending: tuple[int, str] | None = None
    in_symtab = False
    in_reloc_table = False

    def add_symbol(index: int, name: str, groups: tuple[str, ...]):
        val, bind, sec, typ, vis, *rest = groups
        size = rest[0] if rest else None
        symbols.append({"index": index, "name": name, "value": int(val, 16),
                        "bind": bind, "section": sec, "type": typ, "visibility": vis,
                        "size": int(size, 16) if size else None})

    for line in text.splitlines():
        sec = SECTION.match(line)
        if sec:
            if pending:
                warnings.append(f"Unparsed continuation for symbol {pending[0]}: {pending[1]}")
                pending = None
            current = {"index": int(sec[1]), "name": sec[2], "type": sec[3],
                       "flags": sec[4].split(" + ") if sec[4] else [],
                       "size": None, "alignment": None, "address": None}
            sections.append(current)
            in_symtab = current["type"] == "SHT_SYMTAB"
            in_reloc_table = False
            continue
        if current is None:
            continue
        if (m := SIZE.match(line)):
            current["size"] = int(m[1])
            current["alignment"] = int(m[2]) if m[2] else None
        if (m := ADDRESS.match(line)):
            current["address"] = int(m[1], 16)
        if in_symtab:
            if (m := SYMTAB.match(line)):
                symbol_expected = int(m[1])
                continue
            if pending:
                if (m := SYMBOL_TAIL.match(line)):
                    add_symbol(pending[0], pending[1], m.groups())
                    pending = None
                    continue
                if line.strip():
                    warnings.append(f"Unexpected continuation after symbol {pending[0]}")
                    pending = None
            if (m := SYMBOL.match(line)):
                add_symbol(int(m[1]), m[2], m.groups()[2:])
            elif symbol_expected is not None:
                m = SYMBOL_HEAD.match(line)
                if m and not m[2].startswith("0x"):
                    pending = int(m[1]), m[2].strip()
        if current["type"] == "SHT_REL":
            if (m := RELOC_SUMMARY.match(line)):
                reloc_expected[current["index"]] = int(m[1])
                current["relocation_target_index"] = int(m[2])
                current["relocation_target_name"] = m[3]
                continue
            if "Offset    Relocation Type" in line:
                in_reloc_table = True
                continue
            if in_reloc_table:
                if (m := RELOC.match(line)):
                    relocations.append({"rel_section_index": current["index"],
                                        "index": int(m[1]), "offset": int(m[2], 16),
                                        "type_id": int(m[3]), "type": m[4],
                                        "symbol_index": int(m[5]), "symbol": m[6],
                                        "defined_section_index": int(m[7]),
                                        "defined_section_name": m[8]})
                elif (m := RELOC_UNDEFINED.match(line)):
                    relocations.append({"rel_section_index": current["index"],
                                        "index": int(m[1]), "offset": int(m[2], 16),
                                        "type_id": int(m[3]), "type": m[4],
                                        "symbol_index": int(m[5]), "symbol": m[6],
                                        "defined_section_index": None,
                                        "defined_section_name": m[7]})

    if pending:
        warnings.append(f"Unparsed trailing symbol {pending[0]}")
    if symbol_expected is not None and len(symbols) != symbol_expected:
        warnings.append(f"Parsed {len(symbols)} symbols; fromelf reports {symbol_expected}")
    for index, expected in reloc_expected.items():
        actual = sum(r["rel_section_index"] == index for r in relocations)
        if actual != expected:
            warnings.append(f"Relocation section #{index}: parsed {actual}, fromelf reports {expected}")
    if not sections:
        warnings.append("No fromelf section headers parsed; unsupported fromelf output format?")
    return {"sections": sections, "symbols": symbols, "relocations": relocations,
            "section_count": len(sections), "symbol_count": len(symbols),
            "relocation_count": len(relocations), "complete": not warnings,
            "parse_warnings": warnings,
            "representation": "parsed-fromelf-text; not a reconstructed ELF object"}
