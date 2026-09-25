"""Update detection and installation, against throwaway git repositories."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from omacap import updater
from omacap.updater import (
    CHECK_INTERVAL_SECONDS,
    Installation,
    UpdateError,
    UpdateStatus,
    apply_update,
    check_now,
    current_branch,
    find_installation,
    local_revision,
    notice_line,
    pending_update,
    read_status,
    remote_revision,
    working_tree_is_clean,
    write_status,
)


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
        env={"HOME": str(root), "PATH": "/usr/bin:/bin", "GIT_TERMINAL_PROMPT": "0"},
    )


@pytest.fixture
def remote_repo(tmp_path):
    """A bare repo standing in for GitHub, with one commit."""
    work = tmp_path / "origin-work"
    work.mkdir()
    git(work, "init", "--quiet", "--initial-branch", "main")
    git(work, "config", "user.email", "test@example.com")
    git(work, "config", "user.name", "Test")
    (work / "README.md").write_text("first\n")
    git(work, "add", "-A")
    git(work, "commit", "--quiet", "-m", "first")

    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--quiet", "--bare", str(work), str(bare)], check=True)
    return bare, work


@pytest.fixture
def checkout(tmp_path, remote_repo):
    """A clone of that remote, as an installed omacap would be."""
    bare, _ = remote_repo
    path = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--quiet", str(bare), str(path)], check=True)
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    return path


def push_commit(remote_repo, message: str = "second") -> None:
    """Move the remote on, as if someone had pushed."""
    bare, work = remote_repo
    (work / "README.md").write_text(f"{message}\n")
    git(work, "add", "-A")
    git(work, "commit", "--quiet", "-m", message)
    subprocess.run(["git", "-C", str(work), "push", "--quiet", str(bare), "main"], check=True)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("OMACAP_NO_UPDATE_CHECK", raising=False)
    return tmp_path / "cache" / "omacap" / "update-check.json"


# -- reading the checkout -------------------------------------------------

def test_revision_and_branch_are_read(checkout):
    assert len(local_revision(checkout)) >= 7
    assert current_branch(checkout) == "main"


def test_a_clean_tree_is_recognised(checkout):
    assert working_tree_is_clean(checkout)
    (checkout / "scratch.txt").write_text("edited")
    assert not working_tree_is_clean(checkout)


def test_the_remote_tip_is_read(checkout):
    assert remote_revision(checkout) == local_revision(checkout)


def test_the_remote_tip_moves_when_someone_pushes(checkout, remote_repo):
    before = remote_revision(checkout)
    push_commit(remote_repo)
    assert remote_revision(checkout) != before


def test_a_checkout_is_found_from_a_nested_path(checkout):
    nested = checkout / "src" / "omacap"
    nested.mkdir(parents=True)
    assert updater._git_root(nested) == checkout


def test_no_checkout_outside_a_repository(tmp_path):
    assert updater._git_root(tmp_path) is None


def test_the_running_installation_is_identified():
    installation = find_installation()
    assert isinstance(installation, Installation)
    assert installation.description


# -- degrading -------------------------------------------------------------

def test_a_missing_git_never_raises(checkout, monkeypatch, cache):
    """git is only needed to self-update; without it everything else must work."""
    monkeypatch.setenv("PATH", "")
    assert local_revision(checkout) == ""
    assert current_branch(checkout) == ""
    assert remote_revision(checkout) == ""
    assert working_tree_is_clean(checkout) is False
    status = check_now(Installation(checkout=checkout, managed=False))
    assert status.available is False
    assert status.error


def test_an_unreachable_remote_is_reported_not_raised(checkout, remote_repo, cache):
    bare, _ = remote_repo
    git(checkout, "remote", "set-url", "origin", str(bare) + "-gone")
    status = check_now(Installation(checkout=checkout, managed=False))
    assert status.available is False
    assert "could not reach" in status.error


def test_a_non_git_installation_reports_itself(cache):
    status = check_now(Installation(checkout=None, managed=False))
    assert status.available is False
    assert "not a git checkout" in status.error


# -- the cache -------------------------------------------------------------

def test_a_check_is_written_to_the_cache(checkout, cache):
    check_now(Installation(checkout=checkout, managed=False))
    assert cache.is_file()
    assert "checked_at" in json.loads(cache.read_text())


def test_a_written_status_reads_back(cache):
    write_status(UpdateStatus(available=True, local="aaa", remote="bbb",
                              checked_at=time.time()))
    restored = read_status()
    assert restored.available and restored.local == "aaa" and restored.remote == "bbb"


def test_a_missing_cache_reads_as_nothing(cache):
    assert read_status() is None


def test_a_corrupt_cache_reads_as_nothing(cache):
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{not json")
    assert read_status() is None


def test_an_unwritable_cache_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "file-in-the-way"))
    (tmp_path / "file-in-the-way").write_text("blocked")
    write_status(UpdateStatus(available=False, checked_at=time.time()))


def test_freshness(cache):
    assert not UpdateStatus(available=False, checked_at=time.time()).stale
    assert UpdateStatus(
        available=False, checked_at=time.time() - CHECK_INTERVAL_SECONDS - 1
    ).stale


# -- the notice ------------------------------------------------------------

def test_an_available_update_is_offered(checkout, remote_repo, cache, monkeypatch):
    monkeypatch.setattr(
        updater, "find_installation", lambda: Installation(checkout, managed=False)
    )
    push_commit(remote_repo)
    status = check_now()
    assert status.available
    pending = pending_update()
    assert pending is not None
    assert "omacap update" in notice_line(pending)


def test_nothing_is_offered_when_up_to_date(checkout, cache, monkeypatch):
    monkeypatch.setattr(
        updater, "find_installation", lambda: Installation(checkout, managed=False)
    )
    check_now()
    assert pending_update() is None


def test_a_stale_notice_is_dropped_once_the_update_is_installed(
    checkout, remote_repo, cache, monkeypatch
):
    """The cache can outlive the update it was warning about."""
    monkeypatch.setattr(
        updater, "find_installation", lambda: Installation(checkout, managed=False)
    )
    push_commit(remote_repo)
    check_now()
    assert pending_update() is not None

    git(checkout, "pull", "--quiet", "--ff-only", "origin", "main")
    assert pending_update() is None


def test_the_check_can_be_switched_off(checkout, remote_repo, cache, monkeypatch):
    monkeypatch.setattr(
        updater, "find_installation", lambda: Installation(checkout, managed=False)
    )
    push_commit(remote_repo)
    check_now()
    monkeypatch.setenv("OMACAP_NO_UPDATE_CHECK", "1")
    assert pending_update() is None
    assert updater.start_background_check() is None


def test_a_background_check_refreshes_the_cache(checkout, remote_repo, cache, monkeypatch):
    monkeypatch.setattr(
        updater, "find_installation", lambda: Installation(checkout, managed=False)
    )
    push_commit(remote_repo)
    thread = updater.start_background_check()
    thread.join(timeout=30)
    assert read_status().available


# -- applying --------------------------------------------------------------

def test_an_update_is_applied(checkout, remote_repo, cache, monkeypatch):
    push_commit(remote_repo, "the new commit")
    before = local_revision(checkout)
    installs = []
    monkeypatch.setattr(
        updater.subprocess, "run",
        _recording_pip(updater.subprocess.run, installs),
    )

    result = apply_update(Installation(checkout=checkout, managed=False))
    assert result.updated
    assert result.previous == before
    assert result.current != before
    assert any("the new commit" in line for line in result.changes)
    assert installs, "the package should be reinstalled after pulling"


def test_applying_when_already_current_changes_nothing(checkout, cache):
    result = apply_update(Installation(checkout=checkout, managed=False))
    assert not result.updated
    assert "up to date" in result.message.lower()


def test_local_changes_are_never_overwritten(checkout, remote_repo, cache):
    push_commit(remote_repo)
    (checkout / "README.md").write_text("my own edit\n")
    with pytest.raises(UpdateError, match="uncommitted changes"):
        apply_update(Installation(checkout=checkout, managed=False))
    assert (checkout / "README.md").read_text() == "my own edit\n"


def test_a_detached_head_is_refused(checkout, cache):
    git(checkout, "checkout", "--quiet", "--detach", "HEAD")
    with pytest.raises(UpdateError, match="detached HEAD"):
        apply_update(Installation(checkout=checkout, managed=False))


def test_a_non_git_installation_explains_how_to_fix_itself(cache):
    with pytest.raises(UpdateError, match="install script"):
        apply_update(Installation(checkout=None, managed=False))


def test_a_failed_pull_is_reported(checkout, remote_repo, cache):
    bare, _ = remote_repo
    push_commit(remote_repo)
    git(checkout, "remote", "set-url", "origin", str(bare) + "-gone")
    with pytest.raises(UpdateError, match="git pull failed"):
        apply_update(Installation(checkout=checkout, managed=False))


def test_a_failed_reinstall_is_reported(checkout, remote_repo, cache, monkeypatch):
    push_commit(remote_repo)
    real_run = updater.subprocess.run

    def failing(command, *args, **kwargs):
        if "pip" in command:
            return subprocess.CompletedProcess(command, 1, "", "pip exploded")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(updater.subprocess, "run", failing)
    with pytest.raises(UpdateError, match="reinstalling it failed"):
        apply_update(Installation(checkout=checkout, managed=False))


def _recording_pip(real_run, sink):
    def wrapper(command, *args, **kwargs):
        if "pip" in command:
            sink.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")
        return real_run(command, *args, **kwargs)

    return wrapper


def test_one_installs_cache_is_not_shown_to_another(checkout, tmp_path, cache, monkeypatch):
    """Two installs share one cache file; neither may report the other's state."""
    other = tmp_path / "another-install"
    other.mkdir()
    write_status(
        UpdateStatus(
            available=True, local="aaaaaaa", remote="bbbbbbb",
            checked_at=time.time(), checkout=str(other),
        )
    )
    monkeypatch.setattr(
        updater, "find_installation", lambda: Installation(checkout, managed=False)
    )
    monkeypatch.setattr(updater, "start_background_check", lambda: None)
    assert pending_update() is None


def test_a_check_records_which_install_it_was_about(checkout, cache):
    check_now(Installation(checkout=checkout, managed=False))
    assert read_status().checkout == str(checkout)


def test_a_cache_without_a_checkout_is_still_accepted(checkout, remote_repo, cache, monkeypatch):
    """Caches written by an older omacap have no checkout recorded."""
    push_commit(remote_repo)
    write_status(
        UpdateStatus(
            available=True, local=local_revision(checkout), remote="bbbbbbb",
            checked_at=time.time(),
        )
    )
    monkeypatch.setattr(
        updater, "find_installation", lambda: Installation(checkout, managed=False)
    )
    monkeypatch.setattr(updater, "start_background_check", lambda: None)
    assert pending_update() is not None


# -- finding other installations ------------------------------------------

def _make_launcher(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    launcher = directory / "omacap"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    return launcher


def test_launchers_are_found_in_path_order(tmp_path, monkeypatch):
    first = _make_launcher(tmp_path / "first")
    second = _make_launcher(tmp_path / "second")
    monkeypatch.setenv("PATH", f"{first.parent}:{second.parent}")
    assert updater.launchers_on_path() == [first, second]


def test_a_path_without_omacap_finds_nothing(tmp_path, monkeypatch):
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert updater.launchers_on_path() == []


def test_the_same_install_reached_twice_is_listed_once(tmp_path, monkeypatch):
    real = _make_launcher(tmp_path / "real")
    link_dir = tmp_path / "link"
    link_dir.mkdir()
    (link_dir / "omacap").symlink_to(real)
    monkeypatch.setenv("PATH", f"{real.parent}:{link_dir}")
    assert updater.launchers_on_path() == [real]


def test_a_non_executable_file_is_not_a_launcher(tmp_path, monkeypatch):
    directory = tmp_path / "bin"
    directory.mkdir()
    (directory / "omacap").write_text("not executable")
    monkeypatch.setenv("PATH", str(directory))
    assert updater.launchers_on_path() == []


def test_an_empty_path_is_handled(monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert updater.launchers_on_path() == []


def test_the_running_launcher_sits_beside_the_interpreter(tmp_path, monkeypatch):
    venv_bin = tmp_path / "venv" / "bin"
    launcher = _make_launcher(venv_bin)
    monkeypatch.setattr(updater.sys, "executable", str(venv_bin / "python"))
    assert updater.running_launcher() == launcher


def test_no_running_launcher_when_there_is_none(tmp_path, monkeypatch):
    (tmp_path / "bin").mkdir()
    monkeypatch.setattr(updater.sys, "executable", str(tmp_path / "bin" / "python"))
    assert updater.running_launcher() is None
