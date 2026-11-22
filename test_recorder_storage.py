"""Exercise real diagnostic file rotation; no camera or process reads."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parent
class RecorderStorageTests(unittest.TestCase):
    def setUp(self):
        self.helper=ROOT/'camera/ui_recorder_storage.sh'
        self.assertTrue(self.helper.exists(),'bounded recorder storage is not implemented')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.p=Path(self.tmp.name);self.out=self.p/'out';self.out.mkdir()
        self.bb=self.p/'bb';self.bb.write_text('#!/bin/sh\ncmd=$1; shift; exec "$cmd" "$@"\n');self.bb.chmod(0o700)
    def run_script(self,code,success=True):
        env={'OUT':str(self.out),'BB':str(self.bb),'RECORDER_LOG_LIMIT':'512'}
        import os
        r=subprocess.run(['sh','-c','. "$1"\n'+code,'sh',str(self.helper)],
                         env={**os.environ,**env},capture_output=True,text=True)
        if success:self.assertEqual(r.returncode,0,r.stderr)
        return r
    def test_many_snapshots_keep_only_four_slots_per_kind(self):
        src=self.p/'source';src.write_bytes(b'real bounded snapshot')
        self.run_script('i=0; while [ "$i" -lt 30 ]; do\n'
            f' recorder_snapshot journal "$i" "{src}" || exit 8\n'
            f' recorder_snapshot trace "$i" "{src}" || exit 9\n'
            ' i=$((i+1)); done')
        self.assertEqual(len(list(self.out.iterdir())),8)
        self.assertTrue(all(p.read_bytes()==src.read_bytes() for p in self.out.iterdir()))
    def test_long_run_bounds_both_index_logs_and_preserves_latest_record(self):
        self.run_script('i=0; while [ "$i" -lt 100 ]; do\n'
            ' recorder_log "EVENT $i ......................................................" || exit 8\n'
            ' i=$((i+1)); done')
        files=list(self.out.iterdir());self.assertEqual(len(files),2)
        self.assertTrue(all(p.stat().st_size<=512 for p in files))
        self.assertIn('EVENT 99 ',(self.out/'index.log').read_text())
    def test_invalid_or_failed_copy_preserves_previous_complete_slot(self):
        prior=self.out/'journal.0';prior.write_text('previous complete capture')
        r=self.run_script(f'recorder_snapshot journal 4 "{self.p}/missing"',False)
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(prior.read_text(),'previous complete capture')
        self.assertFalse((self.out/'journal.new').exists())
        r=self.run_script(f'recorder_snapshot ../unsafe 0 "{prior}"',False)
        self.assertNotEqual(r.returncode,0)

    def test_rotates_via_ls_fallback_when_busybox_omits_wc(self):
        self.bb.write_text('#!/bin/sh\nif [ "$1" = wc ]; then exit 127; fi\ncmd=$1; shift; exec "$cmd" "$@"\n')
        self.run_script('i=0; while [ "$i" -lt 40 ]; do\n'
            ' recorder_log "EVENT $i ......................................................" || exit 8\n'
            ' i=$((i+1)); done')
        files=list(self.out.iterdir());self.assertEqual(len(files),2)
        self.assertTrue(all(p.stat().st_size<=512 for p in files))
        self.assertIn('EVENT 39 ',(self.out/'index.log').read_text())
if __name__=='__main__':unittest.main(verbosity=2)
