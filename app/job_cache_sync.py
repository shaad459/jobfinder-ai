"""Pulls the shared job cache (refreshed every ~12h by .github/workflows/refresh-job-cache.yml,
see refresh_job_cache.py) into the local `jobs` table, so streamlit_app.py's search can read
already-cached postings instead of waiting on a live Workday/JSearch/Adzuna call every time.

Read-only from this side: clones/pulls a small PUBLIC repo (job postings aren't personal data,
unlike the resume - see resume_sync.py's docstring for why THAT repo has to be private) and
upserts every entry into the local jobs table via repository.save_job(), which already
overwrites-by-url on conflict. No git credentials needed for a public repo pull, unlike
resume_sync.py's push side.
"""
import json
import shutil
import subprocess
from pathlib import Path

from repository import save_job

JOB_CACHE_REPO_URL = "https://github.com/shaad459/jobscout-job-cache.git"
LOCAL_CLONE_DIR = Path.home() / ".jobscout_ai" / "jobscout-job-cache"
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
        pull = _run_git(["pull", "--ff-only"], cwd=LOCAL_CLONE_DIR)
        if pull.returncode != 0:
            return False, f"Couldn't update the local job-cache clone: {pull.stderr.strip()[:300]}"
    else:
        if LOCAL_CLONE_DIR.exists():
            shutil.rmtree(LOCAL_CLONE_DIR)
        clone = _run_git(["clone", JOB_CACHE_REPO_URL, str(LOCAL_CLONE_DIR)], cwd=LOCAL_CLONE_DIR.parent)
        if clone.returncode != 0:
            return False, (
                f"Couldn't clone the job-cache repo (has it been created and refreshed at least "
                f"once yet? See GITHUB_ACTIONS_SETUP.md). git said: {clone.stderr.strip()[:300]}"
            )

    cache_file = LOCAL_CLONE_DIR / CACHE_FILE_NAME
    if not cache_file.exists():
        return False, f"{CACHE_FILE_NAME} doesn't exist yet in the job-cache repo - has refresh-job-cache.yml run at least once?"

    with open(cache_file, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    for job in jobs:
        save_job(job)

    return True, f"Synced {len(jobs)} cached job(s)."
