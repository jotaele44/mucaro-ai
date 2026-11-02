import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.utils import file_lock


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_lock_refuses_redirected_or_nonregular_files_without_blocking(tmp_path, kind):
    path = tmp_path / "state.json"
    lock = tmp_path / "state.json.lock"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep private bytes")
    if kind == "symlink":
        lock.symlink_to(outside)
    else:
        os.mkfifo(lock)
    with pytest.raises(OSError):
        with file_lock(path):
            pytest.fail("a nonregular lock must not be acquired")
    assert outside.read_bytes() == b"keep private bytes"
    lock.unlink()
    with file_lock(path):
        assert lock.stat().st_mode & 0o777 == 0o600


def test_interrupted_operation_releases_the_thread_and_process_lock(tmp_path):
    path = tmp_path / "state.json"
    with pytest.raises(RuntimeError):
        with file_lock(path):
            raise RuntimeError("interrupted before commit")

    def acquire_again():
        with file_lock(path):
            return "acquired"

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(acquire_again).result(timeout=2) == "acquired"
