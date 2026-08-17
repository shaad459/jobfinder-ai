"""Keeps companies_config.json - the git-tracked source of truth for which Workday companies get
searched - in sync between wherever you add/remove a company (normally the Streamlit UI, backed
by the local `companies` SQLite table - see repository.add_company/remove_company) and GitHub,
where refresh_job_cache.py's scheduled run reads it from a fresh checkout with NO access to your
local jobfinder.db.

This gap is real, not hypothetical: GITHUB_ACTIONS_SETUP.md's "one thing worth knowing" section
already documents that the scheduled workflow's database is a completely separate instance from
yours, persisted independently in GitHub's Actions cache. Before this file existed, a company you
added locally would never be searched by anything running on GitHub - only by your own machine.

The repo this pushes to is repo_config.MAIN_REPO_URL, not a constant of its own - see
repo_config.py if you're forking this project for your own use, that's the one file to edit.

Pushes straight to `main` (not a side branch), on purpose - unlike job_cache.json (see
job_cache_sync.py), which refreshes every ~3h and would clutter main's history if committed
there, adding or removing a company is a rare, human-meaningful event worth a normal, visible
commit, the same as any other change you push by hand.

Runs plain `git` subprocess commands against a SEPARATE local clone kept outside the jobfinder-ai
repo tree, for the exact same reason resume_sync.py does: this never touches the actual
D:\...\jobfinder-ai working copy you develop in, so it can't collide with, or accidentally sweep
up, whatever you're mid-edit on there. Uses whatever git credentials are already configured on
this machine - the same ones you've already used to `git push` jobfinder-ai by hand.

Caveat worth knowing upfront (the same tradeoff resume_sync.py accepts for resume-private): since
this pushes straight to origin/main, if you have local commits on your dev machine you haven't
pushed yet, your NEXT manual `git push` may be rejected until you `git pull` first - recoverable,
not destructive, just not silent.
"""
import json
import shutil
import subprocess
from pathlib import Path

from repo_config import MAIN_REPO_URL

REPO_URL = MAIN_REPO_URL
LOCAL_CLONE_DIR = Path.home() / ".jobscout_ai" / "jobfinder-ai-companies-sync"
CONFIG_FILE_NAME = "companies_config.json"
# Relative to the repo root - companies_config.json lives alongside the other app config, not at
# the repo root itself.
CONFIG_REPO_PATH = "app/companies_config.json"


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=60,
    )


def sync_companies_config(companies: dict) -> tuple[bool, str]:
    """companies: the FULL current {name: {company, datacenter, site}} dict - i.e. exactly what
    repository.get_all_companies() returns right now. Always pushes the complete current state
    rather than a diff, so this self-heals if the two ever drift for any reason (e.g. a sync that
    failed partway through last time).

    Never raises - same silent-fallback contract as job_cache_sync.sync_job_cache() and
    resume_sync.sync_resume_for_email_alerts(). Meant to be called right after
    add_company()/remove_company() write to the local DB; a failure here just means the change is
    visible locally but the scheduled workflow won't see it until the next successful sync (or a
    manual `git push` inside LOCAL_CLONE_DIR) - a much smaller regression than crashing the "add
    company" button over a network hiccup.
    """
    try:
        return _sync_companies_config(companies)
    except subprocess.TimeoutExpired:
        return False, "A git command took too long and was cancelled - check your internet connection."
    except Exception as e:
        return False, f"Unexpected error while syncing companies_config.json: {e}"


def _sync_companies_config(companies: dict) -> tuple[bool, str]:
    if shutil.which("git") is None:
        return False, "git isn't installed or isn't on your PATH."

    LOCAL_CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)

    if (LOCAL_CLONE_DIR / ".git").exists():
        pull = _run_git(["pull", "--ff-only"], cwd=LOCAL_CLONE_DIR)
        if pull.returncode != 0:
            return False, (
                f"Couldn't update the local companies-sync clone at {LOCAL_CLONE_DIR} - it may "
                f"have local changes that don't match GitHub. git said: {pull.stderr.strip()[:300]}"
            )
    else:
        if LOCAL_CLONE_DIR.exists():
            shutil.rmtree(LOCAL_CLONE_DIR)
        clone = _run_git(["clone", REPO_URL, str(LOCAL_CLONE_DIR)], cwd=LOCAL_CLONE_DIR.parent)
        if clone.returncode != 0:
            return False, f"Couldn't clone jobfinder-ai: {clone.stderr.strip()[:300]}"

    config_path = LOCAL_CLONE_DIR / CONFIG_REPO_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(companies, f, indent=2, sort_keys=True)
        f.write("\n")

    _run_git(["add", CONFIG_REPO_PATH], cwd=LOCAL_CLONE_DIR)
    status = _run_git(["status", "--porcelain", CONFIG_REPO_PATH], cwd=LOCAL_CLONE_DIR)
    if not status.stdout.strip():
        return True, "companies_config.json on GitHub already matches - nothing to push."

    commit = _run_git(["commit", "-m", "Update configured companies"], cwd=LOCAL_CLONE_DIR)
    if commit.returncode != 0:
        return False, f"Couldn't commit companies_config.json: {commit.stderr.strip()[:300]}"

    push = _run_git(["push"], cwd=LOCAL_CLONE_DIR)
    if push.returncode != 0:
        return False, (
            f"Committed locally but couldn't push (check your GitHub push access, or pull "
            f"first if origin/main moved). git said: {push.stderr.strip()[:300]}"
        )

    return True, "Pushed companies_config.json - the next scheduled cache refresh will pick this up."


def load_companies_config() -> dict:
    """Reads companies_config.json from the SAME checkout this script is currently running in
    (not the separate sync clone above) - this is what refresh_job_cache.py calls. In CI that
    checkout comes fresh from `actions/checkout` on every run, so it always has whatever was most
    recently pushed by sync_companies_config(). Returns {} if the file doesn't exist yet (e.g.
    before this feature's very first company add/remove), so callers should fall back to
    repository.get_all_companies() in that case.
    """
    config_path = Path(__file__).resolve().parent / CONFIG_FILE_NAME
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
