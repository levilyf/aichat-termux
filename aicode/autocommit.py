"""Auto-commit — automatically git commit after the agent makes successful edits.

Only fires when:
  - config.auto_commit.enabled is True
  - the working tree was clean before the turn (if require_clean_tree)
  - the agent actually modified files (write_file, edit_file succeeded)

Generates a short summary from the git diff stat and commits with the
configured message template.
"""

from __future__ import annotations

import subprocess
from typing import Optional

from .config import AutoCommitConfig


def _git(args: list, cwd: str) -> tuple[int, str]:
    """Run a git command and return (returncode, output)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = result.stdout
        if result.stderr:
            out = (out + "\n" + result.stderr).strip() if out else result.stderr.strip()
        return result.returncode, out.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 1, str(e)


def is_git_repo(cwd: str) -> bool:
    rc, _ = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    return rc == 0


def is_clean_tree(cwd: str) -> bool:
    """True if no uncommitted changes (staged or unstaged)."""
    rc, out = _git(["status", "--porcelain"], cwd)
    if rc != 0:
        return False
    return out.strip() == ""


def get_diff_stat(cwd: str) -> str:
    """Return a one-line summary of changes (file count + insertions/deletions)."""
    rc, out = _git(["diff", "--stat", "HEAD"], cwd)
    if rc != 0 or not out:
        return "(no changes)"
    # Take just the summary line
    lines = out.strip().split("\n")
    if lines:
        return lines[-1]
    return "(no changes)"


def generate_summary(cwd: str) -> str:
    """Generate a short summary of what changed."""
    rc, out = _git(["diff", "--stat", "HEAD"], cwd)
    if rc != 0 or not out.strip():
        return "agent changes"
    # Parse the summary line: " 3 files changed, 10 insertions(+), 2 deletions(-)"
    last_line = out.strip().split("\n")[-1]
    return last_line.strip()


def auto_commit_if_needed(
    cwd: str,
    cfg: AutoCommitConfig,
    files_modified: bool,
    was_clean_before: bool,
) -> Optional[str]:
    """Run an auto-commit if conditions are met. Returns the commit message, or None.

    Args:
        cwd: working directory
        cfg: auto-commit config
        files_modified: did the agent actually modify files this turn?
        was_clean_before: was the git tree clean before the agent ran?
    """
    if not cfg.enabled:
        return None
    if not files_modified:
        return None
    if not is_git_repo(cwd):
        return None
    if cfg.require_clean_tree and not was_clean_before:
        return None
    if is_clean_tree(cwd):
        return None  # nothing to commit (shouldn't happen if files_modified=True)

    summary = generate_summary(cwd)
    message = cfg.message_template.format(summary=summary)

    # Stage all changes and commit
    rc, out = _git(["add", "-A"], cwd)
    if rc != 0:
        return None
    rc, out = _git(["commit", "-m", message], cwd)
    if rc != 0:
        return None
    return message
