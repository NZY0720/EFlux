"""Resolve the source identity of the running EFlux process."""

from __future__ import annotations

import os

from eflux.config import PROJECT_ROOT


def repository_git_commit() -> str | None:
    """Resolve the running source revision without invoking a subprocess."""

    configured = os.environ.get("EFLUX_GIT_COMMIT", "").strip()
    if configured:
        return configured
    git_dir = PROJECT_ROOT / ".git"
    head = git_dir / "HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value.startswith("ref: "):
        return value
    ref = value.removeprefix("ref: ").strip()
    try:
        return (git_dir / ref).read_text(encoding="utf-8").strip()
    except OSError:
        try:
            lines = (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            if not line.startswith("#") and line.endswith(f" {ref}"):
                return line.split(" ", 1)[0]
    return None
