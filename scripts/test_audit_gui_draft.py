import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile
import unittest

spec = importlib.util.spec_from_file_location('gui_audit', Path(__file__).with_name('audit_gui_draft.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def make_archive(path, entries, compression=zipfile.ZIP_STORED, timestamp=(2025,1,1,0,0,0)):
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in entries:
            info=zipfile.ZipInfo(name, date_time=timestamp)
            info.compress_type=compression
            z.writestr(info,data)


class FrozenIntakeTests(unittest.TestCase):
    def test_known_uploaded_corpus(self):
        path=Path('/mnt/data/Mucaro AI.zip')
        if path.is_file():
            receipt=audit.audit_archive(path)
            self.assertEqual(receipt['archive_sha256'],audit.EXPECTED_GUI_ZIP_SHA256)
            self.assertEqual(receipt['payload_manifest_sha256'],audit.EXPECTED_PAYLOAD_MANIFEST_SHA256)
            self.assertEqual(receipt['member_count'],137)
    def test_exact_byte_identity(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'x.zip';make_archive(p,[('a.txt',b'a')]);r=audit.audit_archive(p)
            self.assertEqual(audit.compare(r,r)['classification'],'BYTE_IDENTICAL')
    def test_recompression_when_proven_member_metadata_matches(self):
        with TemporaryDirectory() as d:
            a=Path(d)/'a.zip';b=Path(d)/'b.zip'
            make_archive(a,[('a.txt',b'x'*512)],zipfile.ZIP_STORED)
            make_archive(b,[('a.txt',b'x'*512)],zipfile.ZIP_DEFLATED)
            x=audit.compare(audit.audit_archive(a),audit.audit_archive(b))
            self.assertEqual(x['classification'],'PURE_RECOMPRESSION')
            self.assertEqual((x['intersection'],x['a_only'],x['b_only'],x['union'],x['symmetric_difference']),(1,0,0,1,0))
    def test_timestamp_difference_with_same_payload_is_unresolved(self):
        with TemporaryDirectory() as d:
            a=Path(d)/'a.zip';b=Path(d)/'b.zip'
            make_archive(a,[('a.txt',b'a')],timestamp=(2025,1,1,0,0,0))
            make_archive(b,[('a.txt',b'a')],timestamp=(2025,1,2,0,0,0))
            self.assertEqual(audit.compare(audit.audit_archive(a),audit.audit_archive(b))['classification'],'UNRESOLVED')
    def test_path_only_difference(self):
        with TemporaryDirectory() as d:
            a=Path(d)/'a.zip';b=Path(d)/'b.zip'
            make_archive(a,[('a.txt',b'a')]);make_archive(b,[('b.txt',b'a')]);self.assertEqual(audit.compare(audit.audit_archive(a),audit.audit_archive(b))['classification'],'SAME_PAYLOADS_DIFFERENT_PATHS')
    def test_payload_difference(self):
        with TemporaryDirectory() as d:
            a=Path(d)/'a.zip';b=Path(d)/'b.zip'
            make_archive(a,[('a.txt',b'a')]);make_archive(b,[('a.txt',b'b')]);self.assertEqual(audit.compare(audit.audit_archive(a),audit.audit_archive(b))['classification'],'DISTINCT_PAYLOADS')
    def test_traversal_and_cross_platform_collision_fail_closed(self):
        with TemporaryDirectory() as d:
            a=Path(d)/'a.zip';b=Path(d)/'b.zip'
            make_archive(a,[('../escape',b'a')]);make_archive(b,[('a/b',b'a'),('a\\b',b'b')])
            with self.assertRaisesRegex(ValueError,'UNSAFE_PATH'):audit.audit_archive(a)
            with self.assertRaisesRegex(ValueError,'CROSS_PLATFORM_MEMBER_PATH_COLLISION'):audit.audit_archive(b)
    def test_duplicate_filenames_fail_closed(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'x.zip'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('a',b'1');z.writestr('a',b'2')
            with self.assertRaisesRegex(ValueError,'DUPLICATE_MEMBER_PATH'):audit.audit_archive(p)


if __name__=='__main__':unittest.main()
