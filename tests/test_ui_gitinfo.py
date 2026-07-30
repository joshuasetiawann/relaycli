"""Tests for relaycli/ui/gitinfo.py — the status bar's `git:branch ±3`.

This runs inside a render path called up to fifteen times a second, so the
two properties that matter are that it never raises and never actually
forks git fifteen times a second.
"""

from __future__ import annotations

import subprocess

import pytest

from relaycli.ui import gitinfo


@pytest.fixture(autouse=True)
def _clean_cache():
    gitinfo.clear_cache()
    yield
    gitinfo.clear_cache()


def _init_repo(path):
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "seed"], check=True)


def test_reads_the_branch_and_a_clean_tree(tmp_path):
    _init_repo(tmp_path)
    status = gitinfo.read_status(tmp_path)
    assert status.branch == "trunk"
    assert status.dirty == 0


def test_counts_every_changed_path(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "seed.txt").write_text("changed\n")
    (tmp_path / "new.txt").write_text("new\n")
    assert gitinfo.read_status(tmp_path).dirty == 2


def test_outside_a_repository_it_says_nothing_rather_than_raising(tmp_path):
    """The status bar simply drops the git field. A render path is no place
    to raise over a repository question."""
    status = gitinfo.read_status(tmp_path)
    assert status.branch is None
    assert status.dirty == 0


def test_a_missing_git_binary_is_not_an_error(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", explode)
    assert gitinfo.read_status(tmp_path) == gitinfo.RepoStatus()


def test_a_hung_git_is_not_an_error(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1.0)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert gitinfo.read_status(tmp_path) == gitinfo.RepoStatus()


def test_repeat_calls_inside_the_ttl_do_not_fork_git_again(tmp_path):
    """Without this the frame would run two git commands per repaint, at
    fifteen repaints a second, to redraw a string that changes once a
    minute."""
    _init_repo(tmp_path)
    calls = []
    real_run = subprocess.run

    def counting(*args, **kwargs):
        calls.append(args)
        return real_run(*args, **kwargs)

    import unittest.mock

    with unittest.mock.patch.object(subprocess, "run", counting):
        gitinfo.status(tmp_path, now=100.0)
        first = len(calls)
        for _ in range(20):
            gitinfo.status(tmp_path, now=100.0 + gitinfo.CACHE_TTL_S / 2)
        assert len(calls) == first


def test_the_cache_does_expire(tmp_path):
    _init_repo(tmp_path)
    assert gitinfo.status(tmp_path, now=0.0).dirty == 0
    (tmp_path / "seed.txt").write_text("changed\n")
    assert gitinfo.status(tmp_path, now=0.0).dirty == 0, "still inside the TTL"
    assert gitinfo.status(tmp_path, now=gitinfo.CACHE_TTL_S + 1).dirty == 1


def test_two_repositories_do_not_share_one_cache_entry(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _init_repo(a)
    _init_repo(b)
    (b / "seed.txt").write_text("changed\n")
    assert gitinfo.status(a, now=0.0).dirty == 0
    assert gitinfo.status(b, now=0.0).dirty == 1
