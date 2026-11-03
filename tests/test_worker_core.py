import base64
import os
from pathlib import Path
import tempfile
import unittest

from worker_core import ARMCCWorker

MOCK = '''#!/usr/bin/env python3
import pathlib, sys
args = sys.argv[1:]
if '--vsn' in args:
    print('ARM Compiler 5.06u6 build 750')
elif pathlib.Path(sys.argv[0]).stem == 'armcc':
    pathlib.Path(args[args.index('-o') + 1]).write_bytes(b'\\x7fELFfakeobject')
elif pathlib.Path(sys.argv[0]).stem == 'armlink':
    pathlib.Path(args[args.index('--output') + 1]).write_bytes(b'\\x7fELFfakeaxf')
else:
    print('disassembly / symbols')
'''

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root/'bin').mkdir()
        for tool in ('armcc','fromelf','armlink'):
            script=root/'bin'/f'{tool}.exe'
            script.write_text(MOCK)
            script.chmod(0o755)
        self.worker=ARMCCWorker(str(root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_info(self):
        d=self.worker.compiler_info()
        self.assertTrue(all(d['available'].values()))
        self.assertIn('750',d['armcc_version']['stdout'])

    def test_compile_disassemble_artifact_and_link(self):
        d=self.worker.compile_c('int main(void) { return 0; }', 2)
        self.assertTrue(d['ok'], d)
        i=d['object']['artifact_id']
        self.assertEqual(base64.b64decode(self.worker.get_artifact(i)['base64']), b'\x7fELFfakeobject')
        self.assertIn('disassembly',self.worker.inspect_object(i)['stdout'])
        link=self.worker.link_probe([i])
        self.assertTrue(link['ok'],link)
        self.assertEqual(self.worker.get_artifact(link['axf']['artifact_id'])['total_bytes'],11)

    def test_invalid_inputs(self):
        self.assertFalse(self.worker.compile_c('x', 9)['ok'])
        self.assertFalse(self.worker.compile_c('x' * (256*1024+1))['ok'])
        self.assertFalse(self.worker.link_probe([], 'main')['ok'])
        self.assertFalse(self.worker.link_probe(['whatever'], 'main;echo')['ok'])

if __name__ == '__main__':
    unittest.main()
