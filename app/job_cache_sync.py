"""Pulls the shared job cache (refreshed every ~3h by .github/workflows/refresh-job-cache.yml,
see refresh_job_cache.py) into the local `jobs` table, so streamlit_app.py's search can read
already-cached postings instead of waiting on a live Workday/JSearch/Adzuna call every time.

Reads from a dedicated `data-cache` branch of the SAME jobfinder-ai repo, not a separate
repository - an earlier version of this module pointed at a standalone
shaad459/jobscout-job-cache repo that was designed but never actually created, which silently
made every "cache-first" search fall through to a live fetch (the clone step always failed).
Using a branch of the repo that already exists avoids needing to create anything new by hand.
job_cache.json lives there instead of on `main` specifically because it's rewritten every ~3h -
committing that to `main` would flood the repo's real (LinkedIn-visible) commit history with
bot-only churn. See company_sync.py for the contrasting case (companies_config.json, which DOES
belong on `main` since adding a company is rare and human-meaningful).

Read-only from this side: clones/pulls the `data-cache` branch (job postings aren't personal
data, unlike the resume - see resume_sync.py's docstring for why THAT repo has to be private)
and upserts every entry into the local jobs table via repository.save_job(), which already
overwrites-by-url on conflict. No git credentials needed for a public repo pull.

The repo this reads from is repo_config.MAIN_REPO_URL, not a constant of its own - see
repo_config.py if you're forking this project for your own use, that's the one file to edit.
"""
import json
import shutil
import subprocess
from pathlib import Path

from repo_config import MAIN_REPO_URL
from repository import save_job

JOB_CACHE_REPO_URL = MAIN_REPO_URL
JOB_CACHE_BRANCH = "data-cache"
LOCAL_CLONE_DIR = Path.home() / ".jobscout_ai" / "jobfinder-ai-data-cache"
CACHE_FILE_NAME = "job_cache.json"


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=60,
    )


def sync_job_cache() -> tuple[bool, str]:
    """Pulls the latest job_cache.json and upserts its entries into the local `jobs` table.

    Returns (success, message) - never raises, same contract as resume_sync's
    sync_resume_for_email_alerts, since this is meant to be called both from a visible "Refresh
    now" button (where the message matters) and silently on app startup (where a failure should
    just mean "fall back to live search," not crash the app).
    """
    try:
        return _sync_job_cache()
    except subprocess.TimeoutExpired:
        return False, "Pulling the job cache took too long and was cancelled - check your internet connection."
    except Exception as e:
        return False, f"Unexpected error while syncing the job cache: {e}"


def _sync_job_cache() -> tuple[bool, str]:
    if shutil.which("git") is None:
        return False, "git isn't installed or isn't on your PATH."

    LOCAL_CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)

    if (LOCAL_CLONE_DIR / ".git").exists():
        pull = _run_git(["pull", "--ff-only", "origin", JOB_CACHE_BRANCH], cwd=LOCAL_CLONE_DIR)
        if pull.returncode != 0:
            return False, f"Couldn't update the local job-cache clone: {pull.stderr.strip()[:300]}"
    else:
        if LOCAL_CLONE_DIR.exists():
            shutil.rmtree(LOCAL_CLONE_DIR)
        clone = _run_git(
            ["clone", "--branch", JOB_CACHE_BRANCH, "--single-branch", JOB_CACHE_REPO_URL, str(LOCAL_CLONE_DIR)],
            cwd=LOCAL_CLONE_DIR.parent,
        )
        if clone.returncode != 0:
            return False, (
                f"Couldn't clone the '{JOB_CACHE_BRANCH}' branch (has "
                f"refresh-job-cache.yml run at least once yet? It creates this branch on its "
                f"first run). git said: {clone.stderr.strip()[:300]}"
            )

    cache_file = LOCAL_CLONE_DIR / CACHE_FILE_NAME
    if not cache_file.exists():
        return False, f"{CACHE_FILE_NAME} doesn't exist yet on the '{JOB_CACHE_BRANCH}' branch - has refresh-job-cache.yml run at least once?"

    with open(cache_file, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    for job in jobs:
        save_job(job)

    return True, f"Synced {len(jobs)} cached job(s)."
