import hashlib
import os
import shutil
import tempfile
import unittest

from directory_difference import DirectoryDiff, DiffResult, FileEntry


class _TempTree:
    """Context manager that creates a temp directory and tears it down."""

    def __enter__(self):
        self.root = tempfile.mkdtemp(prefix="dirdiff_")
        return self.root

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)


def _write(root, relpath, content):
    full = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(content if isinstance(content, bytes) else content.encode())
    return full


def _sha256(b):
    return hashlib.sha256(b).hexdigest()


class TestHappyPath(unittest.TestCase):
    def test_identical_trees(self):
        with _TempTree() as a, _TempTree() as b:
            _write(a, "x.txt", b"hello")
            _write(a, "sub/y.txt", b"world")
            _write(b, "x.txt", b"hello")
            _write(b, "sub/y.txt", b"world")
            diff = DirectoryDiff().compare(a, b)
            self.assertEqual(diff.added, [])
            self.assertEqual(diff.removed, [])
            self.assertEqual(diff.modified, [])

    def test_added_file(self):
        with _TempTree() as a, _TempTree() as b:
            _write(a, "keep.txt", b"same")
            _write(b, "keep.txt", b"same")
            _write(b, "new.txt", b"fresh")
            diff = DirectoryDiff().compare(a, b)
            self.assertEqual(len(diff.added), 1)
            self.assertEqual(diff.added[0].relative_path, "new.txt")
            self.assertEqual(diff.added[0].hash, _sha256(b"fresh"))
            self.assertEqual(diff.added[0].size, 5)
            self.assertEqual(diff.removed, [])
            self.assertEqual(diff.modified, [])

    def test_removed_file(self):
        with _TempTree() as a, _TempTree() as b:
            _write(a, "gone.txt", b"bye")
            _write(a, "keep.txt", b"same")
            _write(b, "keep.txt", b"same")
            diff = DirectoryDiff().compare(a, b)
            self.assertEqual(len(diff.removed), 1)
            self.assertEqual(diff.removed[0].relative_path, "gone.txt")
            self.assertEqual(diff.added, [])
            self.assertEqual(diff.modified, [])

    def test_modified_file(self):
        with _TempTree() as a, _TempTree() as b:
            _write(a, "change.txt", b"original")
            _write(b, "change.txt", b"changed!!")
            diff = DirectoryDiff().compare(a, b)
            self.assertEqual(len(diff.modified), 1)
            self.assertEqual(diff.modified[0].relative_path, "change.txt")
            self.assertEqual(diff.modified[0].hash, _sha256(b"changed!!"))
            self.assertEqual(diff.modified[0].size, 9)
            self.assertEqual(diff.added, [])
            self.assertEqual(diff.removed, [])


class TestEdgeCases(unittest.TestCase):
    def test_empty_directories(self):
        with _TempTree() as a, _TempTree() as b:
            diff = DirectoryDiff().compare(a, b)
            self.assertIsInstance(diff, DiffResult)
            self.assertEqual(diff.added, [])
            self.assertEqual(diff.removed, [])
            self.assertEqual(diff.modified, [])

    def test_nested_subdirectories(self):
        with _TempTree() as a, _TempTree() as b:
            _write(a, os.path.join("d1", "d2", "f.txt"), b"deep")
            _write(b, os.path.join("d1", "d2", "f.txt"), b"deep")
            _write(b, os.path.join("d1", "d3", "g.txt"), b"deeper")
            diff = DirectoryDiff().compare(a, b)
            self.assertEqual(len(diff.added), 1)
            expected = os.path.join("d1", "d3", "g.txt")
            self.assertEqual(diff.added[0].relative_path, expected)

    def test_symlink_is_skipped(self):
        # Symlinks to regular files must NOT be treated as files. Only true
        # regular files are hashed.
        with _TempTree() as a, _TempTree() as b:
            _write(a, "real.txt", b"data")
            target = os.path.join(a, "real.txt")
            link = os.path.join(a, "link.txt")
            try:
                os.symlink(target, link)
            except OSError:
                self.skipTest("symlinks not supported on this platform")
            _write(b, "real.txt", b"data")
            diff = DirectoryDiff().compare(a, b)
            # Only real.txt is compared; link.txt is ignored entirely.
            self.assertEqual(diff.added, [])
            self.assertEqual(diff.removed, [])
            self.assertEqual(diff.modified, [])

    def test_large_file_multichunk(self):
        # Use a tiny chunk size to force multiple read iterations.
        payload = os.urandom(100_000)
        with _TempTree() as a, _TempTree() as b:
            _write(a, "big.bin", payload)
            _write(b, "big.bin", payload)
            diff = DirectoryDiff(chunk_size=1024).compare(a, b)
            self.assertEqual(diff.modified, [])
            # Tamper the right copy.
            _write(b, "big.bin", payload + b"x")
            diff2 = DirectoryDiff(chunk_size=1024).compare(a, b)
            self.assertEqual(len(diff2.modified), 1)
            self.assertEqual(diff2.modified[0].hash, hashlib.sha256(payload + b"x").hexdigest())

    def test_results_are_sorted(self):
        with _TempTree() as a, _TempTree() as b:
            names = ["c.txt", "a.txt", "b.txt"]
            for n in names:
                _write(a, n, b"x")
                _write(b, n, b"y")
            diff = DirectoryDiff().compare(a, b)
            got = [e.relative_path for e in diff.modified]
            self.assertEqual(got, sorted(names))

    def test_result_types(self):
        with _TempTree() as a, _TempTree() as b:
            _write(a, "f.txt", b"a")
            _write(b, "f.txt", b"b")
            diff = DirectoryDiff().compare(a, b)
            self.assertIsInstance(diff, DiffResult)
            self.assertIsInstance(diff.modified[0], FileEntry)
            self.assertIsInstance(diff.modified[0].relative_path, str)
            self.assertIsInstance(diff.modified[0].size, int)
            self.assertIsInstance(diff.modified[0].hash, str)


class TestErrorHandling(unittest.TestCase):
    def test_left_path_missing(self):
        with _TempTree() as b:
            with self.assertRaises(FileNotFoundError):
                DirectoryDiff().compare("/nonexistent/path/xyz", b)

    def test_right_path_missing(self):
        with _TempTree() as a:
            with self.assertRaises(FileNotFoundError):
                DirectoryDiff().compare(a, "/nonexistent/path/xyz")

    def test_path_is_file_not_directory(self):
        with _TempTree() as a:
            f = _write(a, "notdir.txt", b"x")
            with self.assertRaises(NotADirectoryError):
                DirectoryDiff().compare(a, f)

    def test_invalid_hash_name(self):
        with self.assertRaises(ValueError):
            DirectoryDiff(hash_name="not-a-real-algorithm")

    def test_invalid_chunk_size(self):
        with self.assertRaises(ValueError):
            DirectoryDiff(chunk_size=0)
        with self.assertRaises(ValueError):
            DirectoryDiff(chunk_size=-1)


class TestCustomHash(unittest.TestCase):
    def test_md5_hash(self):
        with _TempTree() as a, _TempTree() as b:
            _write(a, "f.txt", b"content")
            _write(b, "f.txt", b"content")
            diff = DirectoryDiff(hash_name="md5").compare(a, b)
            self.assertEqual(diff.added, [])
            self.assertEqual(diff.removed, [])
            self.assertEqual(diff.modified, [])
            # Confirm the md5 hash is actually used by introducing a change.
            _write(b, "f.txt", b"different")
            diff2 = DirectoryDiff(hash_name="md5").compare(a, b)
            self.assertEqual(diff2.modified[0].hash, hashlib.md5(b"different").hexdigest())


if __name__ == "__main__":
    unittest.main()
