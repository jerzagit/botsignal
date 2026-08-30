"""Safe git metadata for dataset/run journals (no secrets)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(args: list[str], cwd: Path | None = None) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def git_branch(repo: Path | None = None) -> str | None:
    return _git(["branch", "--show-current"], cwd=repo)


def git_commit(repo: Path | None = None) -> str | None:
    return _git(["rev-parse", "HEAD"], cwd=repo)
