"""git: timestamp of the last commit in a project folder."""
import shutil
import subprocess
import time
from pathlib import Path

from . import health


def _has_git_metadata(path):
    try:
        p = Path(path)
        if not p.is_absolute():
            p = p.resolve()
        return any((parent / ".git").exists() or (parent / ".git").is_symlink() for parent in (p, *p.parents))
    except (OSError, RuntimeError, ValueError):
        return True


class Git:
    """Collects per-project lookups so failures show up in source health (a non-repo folder is not a failure)."""

    def __init__(self, deadline=None):
        """deadline: time.monotonic() value after which lookups are skipped (and counted as failed)."""
        self.exe = shutil.which("git")
        self.errors = 0
        self.deadline = deadline

    def last_commit(self, path):
        if not path or not self.exe:
            return 0
        left = 10 if self.deadline is None else min(10, self.deadline - time.monotonic())
        if left <= 0:
            self.errors += 1
            return 0
        try:
            r = subprocess.run([self.exe, "-C", path, "log", "-1", "--format=%ct"],
                               capture_output=True, text=True, timeout=left)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.errors += 1
            return 0
        if r.returncode:
            message = (r.stderr or "").lower()
            if "does not have any commits yet" in message:
                return 0
            if "not a git repository" in message and not _has_git_metadata(path):
                return 0
            self.errors += 1
            return 0
        try:
            return int(r.stdout.strip()) if r.stdout.strip() else 0
        except ValueError:
            self.errors += 1
            return 0

    def health(self):
        if not self.exe:
            return health("absent", "git not on PATH")
        return health("degraded", f"{self.errors} lookups failed") if self.errors else health("ok")
