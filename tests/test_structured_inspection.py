"""Exercise the fromelf text parser on a representative ARMCC5 layout."""
import unittest
from structured_inspection import parse_fromelf


SAMPLE = '''
** Section #1 '.text' (SHT_PROGBITS) [SHF_ALLOC + SHF_EXECINSTR]
    Size   : 24 bytes (alignment 4)
    Address: 0x00000000
** Section #2 '.rel.text' (SHT_REL)
    Size   : 8 bytes (alignment 4)
    1 relocations applied to section #1 '.text'
    #      Offset    Relocation Type               Wrt Symbol                   Defined in
    ========================================================================
      0  0x00000014    2 R_ARM_ABS32               2 .data                    #3 '.data'
** Section #3 '.data' (SHT_PROGBITS) [SHF_ALLOC + SHF_WRITE]
    Size   : 4 bytes
** Section #4 '.symtab' (SHT_SYMTAB)
    Symbol table .symtab (3 symbols, 2 local)
      #  Symbol Name                Value      Bind  Sec  Type  Vis  Size
    ========================================================================
      1  module.c                   0x00000000   Lc  Abs  File  De
      2  .data                      0x00000000   Lc    3  Sect  De   0x4
      3  VeryLongSymbolNameWithOmittedValueOnFirstLine
                                    0x00000001   Gb    1  Code  Hi   0x10
'''


class ParserTests(unittest.TestCase):
    def test_complete_sections_symbols_and_relocations(self):
        result = parse_fromelf(SAMPLE)
        self.assertTrue(result["complete"], result["parse_warnings"])
        self.assertEqual(result["section_count"], 4)
        self.assertEqual(result["symbol_count"], 3)
        self.assertEqual(result["relocation_count"], 1)
        self.assertEqual(result["sections"][0]["flags"], ["SHF_ALLOC", "SHF_EXECINSTR"])
        self.assertEqual(result["symbols"][-1]["name"], "VeryLongSymbolNameWithOmittedValueOnFirstLine")
        self.assertEqual(result["symbols"][-1]["size"], 16)
        self.assertEqual(result["relocations"][0]["symbol"], ".data")
        self.assertEqual(result["relocations"][0]["defined_section_index"], 3)

    def test_warns_when_not_all_symbols_parsed(self):
        data = parse_fromelf(SAMPLE.replace("(3 symbols, 2 local)", "(4 symbols, 2 local)"))
        self.assertFalse(data["complete"])
        self.assertIn("Parsed 3 symbols", data["parse_warnings"][0])

    def test_warns_when_unrecognized(self):
        data = parse_fromelf("unsupported\n")
        self.assertFalse(data["complete"])
        self.assertEqual(data["section_count"], 0)

if __name__ == "__main__":
    unittest.main()
