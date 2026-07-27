"""Unit tests for relaycli.tools.cache.FileCache, independent of any
specific tool. Integration with read_file/search/write_file/edit_file/
apply_patch is covered in test_tools.py."""

from __future__ import annotations

import os
import time

from relaycli.tools.cache import FileCache


def test_read_bytes_returns_file_contents(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello")
    cache = FileCache()
    assert cache.read_bytes(f) == b"hello"


def test_second_read_is_a_hit_when_unchanged(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello")
    cache = FileCache()
    cache.read_bytes(f)
    cache.read_bytes(f)
    assert cache.hits == 1
    assert cache.misses == 1


def test_mtime_and_size_change_forces_a_fresh_read_without_explicit_invalidate(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello")
    cache = FileCache()
    assert cache.read_bytes(f) == b"hello"

    # Force a distinct mtime — some filesystems have 1s resolution.
    later = time.time() + 5
    f.write_bytes(b"goodbye, much longer now")
    os.utime(f, (later, later))

    assert cache.read_bytes(f) == b"goodbye, much longer now"
    assert cache.misses == 2
    assert cache.hits == 0


def test_invalidate_forces_a_fresh_read_even_if_stat_would_not_have_changed(tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_bytes(b"AAAAA")
    cache = FileCache()
    cache.read_bytes(f)

    # Same-length overwrite: simulate the narrow race where mtime/size don't
    # change (or land inside filesystem timestamp resolution) by patching
    # stat() to report identical (mtime, size) even though content changed.
    real_stat = f.stat()
    f.write_bytes(b"BBBBB")

    class _FakeStat:
        st_mtime = real_stat.st_mtime
        st_size = real_stat.st_size

    monkeypatch.setattr(type(f), "stat", lambda self: _FakeStat())
    # Without invalidate(), this would incorrectly serve the stale "AAAAA".
    cache.invalidate(f)
    assert cache.read_bytes(f) == b"BBBBB"


def test_invalidate_unknown_path_is_a_no_op(tmp_path):
    cache = FileCache()
    cache.invalidate(tmp_path / "never-read.txt")  # must not raise


def test_clear_resets_entries_and_counters(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello")
    cache = FileCache()
    cache.read_bytes(f)
    cache.read_bytes(f)
    assert cache.hits == 1

    cache.clear()
    assert cache.hits == 0
    assert cache.misses == 0
    cache.read_bytes(f)
    assert cache.misses == 1  # re-reads from disk, not a stale hit


def test_read_bytes_raises_oserror_for_missing_file(tmp_path):
    cache = FileCache()
    try:
        cache.read_bytes(tmp_path / "does-not-exist.txt")
        assert False, "expected OSError"
    except OSError:
        pass
