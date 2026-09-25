"""Finding out whether a newer omacap exists, and installing it.

The check talks to the project's own git remote and nothing else. It is cached,
runs in the background, and every failure is silent: being offline, having no
remote, or sitting on a detached HEAD must never get in the way of recording.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

#: Where the install script puts the checkout it manages.
MANAGED_ROOT = Path.home() / ".local" / "share" / "omacap"
MANAGED_CHECKOUT = MANAGED_ROOT / "src"

#: How long a check stays fresh. Startup reads the cache and, when it is older
#: than this, refreshes it in the background - so a notice appears on the next
#: run rather than delaying this one.
CHECK_INTERVAL_SECONDS = 24 * 60 * 60

GIT_TIMEOUT = 20.0
INSTALL_TIMEOUT = 300.0

#: Set to any non-empty value to switch the check off entirely.
OPT_OUT_ENV = "OMACAP_NO_UPDATE_CHECK"


class UpdateError(RuntimeError):
    """Raised when an update was attempted but could not be completed."""


@dataclass(frozen=True)
class Installation:
    """Where the running omacap came from."""

    checkout: Path | None
    managed: bool

    @property
    def updatable(self) -> bool:
        return self.checkout is not None

    @property
    def description(self) -> str:
        if self.checkout is None:
            return "not a git checkout"
        kind = "managed" if self.managed else "git checkout"
        return f"{kind} at {self.checkout}"


@dataclass(frozen=True)
class UpdateStatus:
    """The result of asking the remote whether anything moved."""

    available: bool
    local: str = ""
    remote: str = ""
    checked_at: float = 0.0
    error: str = ""
    #: Which checkout this was about. The cache is per user, but a machine can
    #: hold several installs, and one must not report another's revisions.
    checkout: str = ""

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.checked_at)

    @property
    def stale(self) -> bool:
        return self.age > CHECK_INTERVAL_SECONDS


def check_disabled() -> bool:
    return bool(os.environ.get(OPT_OUT_ENV))


# -- locating the installation -------------------------------------------

def _git_root(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def find_installation() -> Installation:
    """Work out whether the running omacap can update itself.

    An editable install imports straight out of the checkout, so walking up from
    the package finds it. A plain install does not, in which case the checkout
    the install script manages is the next best guess.
    """
    from . import __file__ as package_file

    root = _git_root(Path(package_file).resolve().parent)
    if root is not None:
        return Installation(checkout=root, managed=root == MANAGED_CHECKOUT)
    if (MANAGED_CHECKOUT / ".git").exists():
        return Installation(checkout=MANAGED_CHECKOUT, managed=True)
    return Installation(checkout=None, managed=False)


def running_launcher() -> Path | None:
    """The ``omacap`` command belonging to the interpreter now running."""
    candidate = Path(sys.executable).resolve().parent / "omacap"
    return candidate if candidate.exists() else None


def launchers_on_path() -> list[Path]:
    """Every ``omacap`` command reachable on PATH, in the order PATH gives them.

    More than one means whichever comes first wins, which is worth saying out
    loud when someone has installed omacap twice.
    """
    found: list[Path] = []
    seen: set[Path] = set()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / "omacap"
        try:
            if not (candidate.is_file() and os.access(candidate, os.X_OK)):
                continue
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        found.append(candidate)
    return found


# -- git helpers ----------------------------------------------------------

def _git(root: Path, *args: str, timeout: float = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    """Run git, reporting failure rather than raising.

    git may be missing entirely - omacap only needs it to update itself, so its
    absence must cost the update check and nothing else.
    """
    command = ["git", "-C", str(root), *args]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, 127, "", "git is not installed")
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(command, 1, "", "git could not be run")


def local_revision(root: Path) -> str:
    result = _git(root, "rev-parse", "--short", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else ""


def current_branch(root: Path) -> str:
    result = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = result.stdout.strip() if result.returncode == 0 else ""
    return "" if branch == "HEAD" else branch


def working_tree_is_clean(root: Path) -> bool:
    result = _git(root, "status", "--porcelain")
    return result.returncode == 0 and not result.stdout.strip()


def remote_revision(root: Path, timeout: float = GIT_TIMEOUT) -> str:
    """The remote's tip for the current branch. Empty when it cannot be reached."""
    branch = current_branch(root)
    if not branch:
        return ""
    result = _git(root, "ls-remote", "origin", branch, timeout=timeout)
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    return result.stdout.split()[0][: len(local_revision(root)) or 7]


# -- the cache ------------------------------------------------------------

def cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "omacap" / "update-check.json"


def read_status() -> UpdateStatus | None:
    """The last check's result, or None when there is nothing usable."""
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return UpdateStatus(
            available=bool(data.get("available", False)),
            local=str(data.get("local", "")),
            remote=str(data.get("remote", "")),
            checked_at=float(data.get("checked_at", 0.0)),
            error=str(data.get("error", "")),
            checkout=str(data.get("checkout", "")),
        )
    except (TypeError, ValueError):
        return None


def write_status(status: UpdateStatus) -> None:
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "available": status.available,
                    "local": status.local,
                    "remote": status.remote,
                    "checked_at": status.checked_at,
                    "error": status.error,
                    "checkout": status.checkout,
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass          # a cache we cannot write is not worth an error


# -- checking -------------------------------------------------------------

def check_now(installation: Installation | None = None) -> UpdateStatus:
    """Ask the remote whether anything moved, and remember the answer."""
    installation = installation or find_installation()
    if installation.checkout is None:
        status = UpdateStatus(
            available=False, checked_at=time.time(), error="not a git checkout"
        )
        write_status(status)
        return status

    root = installation.checkout
    local = local_revision(root)
    remote = remote_revision(root)
    status = UpdateStatus(
        available=bool(local and remote and local != remote),
        local=local,
        remote=remote,
        checked_at=time.time(),
        error="" if remote else "could not reach the remote",
        checkout=str(root),
    )
    write_status(status)
    return status


def start_background_check() -> threading.Thread | None:
    """Refresh the cache without holding anything up. Never raises."""
    if check_disabled():
        return None

    def worker() -> None:
        try:
            check_now()
        except Exception:
            pass      # a failed check must never surface anywhere

    thread = threading.Thread(target=worker, daemon=True, name="omacap-update-check")
    thread.start()
    return thread


def pending_update() -> UpdateStatus | None:
    """A cached, still-relevant update notice, refreshing the cache if stale.

    Returns something only when an update really is waiting; the refresh it may
    start is for the *next* run.
    """
    if check_disabled():
        return None
    status = read_status()
    installation = find_installation()
    current = str(installation.checkout) if installation.checkout else ""

    # A cache written by a different install on the same machine says nothing
    # about this one, and must not be shown or trusted as fresh.
    if status is not None and status.checkout and status.checkout != current:
        status = None

    if status is None or status.stale:
        start_background_check()
    if status is None or not status.available:
        return None
    # The cache may also pre-date an update that has since been installed.
    if installation.checkout is not None:
        if local_revision(installation.checkout) == status.remote:
            return None
    return status


def notice_line(status: UpdateStatus) -> str:
    return (
        f"An update is available ({status.local} → {status.remote}). "
        f"Run: omacap update"
    )


# -- applying -------------------------------------------------------------

@dataclass
class UpdateResult:
    """What an update actually did."""

    updated: bool
    previous: str
    current: str
    changes: list[str]
    message: str


def apply_update(installation: Installation | None = None) -> UpdateResult:
    """Fast-forward the checkout and reinstall it."""
    installation = installation or find_installation()
    if installation.checkout is None:
        raise UpdateError(
            "omacap was not installed from a git checkout, so it cannot update "
            "itself. Reinstall with the install script to enable updates."
        )
    root = installation.checkout

    if not working_tree_is_clean(root):
        raise UpdateError(
            f"{root} has uncommitted changes. Commit or stash them first - "
            f"updating would overwrite them."
        )
    branch = current_branch(root)
    if not branch:
        raise UpdateError(
            f"{root} is not on a branch (detached HEAD), so there is nothing to "
            f"fast-forward. Check out a branch first."
        )

    previous = local_revision(root)
    pull = _git(root, "pull", "--ff-only", "origin", branch, timeout=INSTALL_TIMEOUT)
    if pull.returncode != 0:
        detail = (pull.stderr or pull.stdout).strip().splitlines()
        raise UpdateError(
            "git pull failed: " + (detail[-1] if detail else "unknown error")
        )

    current = local_revision(root)
    if current == previous:
        return UpdateResult(
            updated=False,
            previous=previous,
            current=current,
            changes=[],
            message="Already up to date.",
        )

    log = _git(root, "log", "--oneline", f"{previous}..{current}")
    changes = log.stdout.strip().splitlines() if log.returncode == 0 else []

    reinstall = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--editable", str(root)],
        capture_output=True,
        text=True,
        timeout=INSTALL_TIMEOUT,
    )
    if reinstall.returncode != 0:
        detail = (reinstall.stderr or reinstall.stdout).strip().splitlines()
        raise UpdateError(
            "the code was updated but reinstalling it failed: "
            + (detail[-1] if detail else "unknown error")
            + f". Try: {sys.executable} -m pip install --editable {root}"
        )

    write_status(
        UpdateStatus(
            available=False,
            local=current,
            remote=current,
            checked_at=time.time(),
            checkout=str(root),
        )
    )
    return UpdateResult(
        updated=True,
        previous=previous,
        current=current,
        changes=changes,
        message=f"Updated {previous} → {current}.",
    )
