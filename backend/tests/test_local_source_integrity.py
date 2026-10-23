"""Local source HEAD/tree/blob provenance adversarial tests."""
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.app.local_source_integrity import attest_local_source,SourceIntegrityError

def git(root,*args):
    return subprocess.run(["git","-C",str(root),*args],check=True,
                          capture_output=True,text=True).stdout.strip()

class LocalSourceIntegrityTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        git(self.root,"init","-q")
        git(self.root,"config","user.email","test@example.invalid")
        git(self.root,"config","user.name","test-identity")
        self.module=self.root/"model.py"
        self.module.write_text("MODEL=1\n",encoding="utf8")
        git(self.root,"add","model.py")
        git(self.root,"commit","-qm","frozen source")
        self.head=git(self.root,"rev-parse","HEAD")
        self.sha="sha256:"+hashlib.sha256(self.module.read_bytes()).hexdigest()

    def attest(self,**changes):
        k=dict(project_root=self.root,expected_head=self.head,
               source_path="model.py",expected_sha256=self.sha)
        k.update(changes)
        return attest_local_source(**k)

    def test_exact_clean_git_tree_and_bytes(self):
        evidence=self.attest()
        self.assertEqual(evidence["head"],self.head)
        self.assertEqual(evidence["tree"],git(self.root,"rev-parse","HEAD^{tree}"))
        self.assertTrue(evidence["tracked_blob_equivalent"])
        self.assertFalse(evidence["independent_remote_attestation"])

    def test_wrong_head_denied(self):
        with self.assertRaisesRegex(SourceIntegrityError,"SOURCE_HEAD_MISMATCH"):
            self.attest(expected_head="a"*40)

    def test_dirty_source_file_denied(self):
        self.module.write_text("MODEL=999\n")
        with self.assertRaisesRegex(SourceIntegrityError,"DIRTY_SOURCE_CHECKOUT"):
            self.attest()

    def test_untracked_fixtures_block_attestation(self):
        (self.root/"unexpected.py").write_text("x=1\n")
        with self.assertRaisesRegex(SourceIntegrityError,"DIRTY_SOURCE_CHECKOUT"):
            self.attest()

    def test_wrong_byte_expectation_denied(self):
        with self.assertRaisesRegex(SourceIntegrityError,"SOURCE_FILE_HASH_MISMATCH"):
            self.attest(expected_sha256="sha256:"+"0"*64)

    def test_path_escape_denied(self):
        with self.assertRaisesRegex(SourceIntegrityError,"SOURCE_PATH_OUTSIDE_OR_MISSING"):
            self.attest(source_path="../missing.py")

    def test_untracked_source_not_eligible(self):
        with self.assertRaisesRegex(SourceIntegrityError,"SOURCE_PATH_OUTSIDE_OR_MISSING"):
            self.attest(source_path="absent.py")

    def test_nested_git_root_denied(self):
        sub=self.root/"nested"
        sub.mkdir()
        with self.assertRaisesRegex(SourceIntegrityError,"PROJECT_GIT_ROOT_MISMATCH"):
            self.attest(project_root=sub,source_path="../model.py")

    def test_missing_git_repo_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"model.py").write_text("MODEL=1\n")
            with self.assertRaisesRegex(SourceIntegrityError,"GIT_SOURCE_IDENTITY_UNAVAILABLE"):
                self.attest(project_root=root)

if __name__=="__main__":
    unittest.main()
