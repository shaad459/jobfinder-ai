"""Keeps search_queries_config.json - the git-tracked source of truth for which role titles the
3-hourly job-cache refresh searches for - in sync with whichever resumes are currently active in
your library. Mirrors company_sync.py's design and reasoning exactly: refresh_job_cache.py runs
inside GitHub Actions, which checks out a fresh copy of the repo with no access to your local
jobfinder.db, so a title from a resume you just uploaded needs a git-tracked path to reach it -
the local `profiles` table alone isn't visible there.

Before this file existed, refresh_job_cache.py's query terms were a hardcoded, single-purpose
list (see BOOTSTRAP_DEFAULT_QUERIES below) - accurate for the resumes this project was built
around, but silently wrong for any other role family: the cache would simply never fetch
anything relevant, and cache-first search would look broken without ever surfacing why. A
software engineer's resume, for example, would cache-miss on every search under the old
hardcoded list, always falling back to a slow live fetch with no visible explanation.

sync_search_queries() takes the FULL current set of active resumes' job_titles (i.e. exactly
what repository.list_profiles(active_only=True) returns right now), not a diff - so retiring a
resume removes its titles from the next push the same way uploading one adds them, and the file
always reflects "what's actually active," never an accumulation of everything ever uploaded.
This is also what makes cache replacement automatic: refresh_job_cache.py already rewrites
job_cache.json from scratch every run based on whatever queries apply at that moment (it was
never an incremental merge) - so the very next scheduled run after a query-set change naturally
drops jobs from titles that are no longer active, with no separate "delete the old cache" step
needed here. The local `jobs` table (job_cache_sync.py's target) ages out old entries via the
existing delete_stale_jobs() call at app startup instead of an explicit purge tied to this - a
title-based purge risked deleting match history for a job still worth acting on, for a
correctness benefit (a slightly faster fade-out of stale postings) not worth that risk.

Pushes straight to `main`, same as company_sync.py and for the same reason: this changes rarely
(only on resume upload/retire) and is human-meaningful, unlike job_cache.json's every-3h churn.

The repo this pushes to is repo_config.MAIN_REPO_URL, not a constant of its own - see
repo_config.py if you're forking this project for your own use, that's the one file to edit.
"""
import json
import shutil
import subprocess
from pathlib import Path

from repo_config import MAIN_REPO_URL

REPO_URL = MAIN_REPO_URL
LOCAL_CLONE_DIR = Path.home() / ".jobscout_ai" / "jobfinder-ai-queries-sync"
CONFIG_FILE_NAME = "search_queries_config.json"
# Relative to the repo root - alongside companies_config.json.
CONFIG_REPO_PATH = "app/search_queries_config.json"

# Used ONLY when search_queries_config.json has never been synced at all (a repo checkout from
# before this feature existed, or before any resume has ever gone through
# get_or_create_profile/set_profile_active) - NOT a fallback for "zero active resumes right
# now," which legitimately means "nothing to search" and should push/return an empty list, not
# silently keep searching someone else's roles forever.
BOOTSTRAP_DEFAULT_QUERIES = ["product owner", "product manager", "business analyst"]

# Caps how many distinct titles feed one refresh run - each title is one more query x company
# combination fetched from Workday/JSearch/Adzuna, so an unbounded union across a large resume
# library could make every 3-hourly run slow (or trip rate limits on the search APIs
# themselves). Logged, never silent, when it actually triggers - see _extract_queries.
MAX_QUERIES = 12


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=60,
    )


def _extract_queries(active_profiles: list[dict]) -> list[str]:
    """De-dupes case-insensitively across ALL active profiles' job_titles, keeping first-seen
    casing for readability. Order follows repository.list_profiles()'s own order (newest resume
    first), so if MAX_QUERIES ever truncates, it favors your most recently active resumes rather
    than an arbitrary cut.
    """
    seen = {}
    for profile in active_profiles:
        for title in profile.get("job_titles") or []:
            title = (title or "").strip()
            if not title:
                continue
            key = title.lower()
            if key not in seen:
                seen[key] = title
    queries = list(seen.values())
    if len(queries) > MAX_QUERIES:
        dropped = queries[MAX_QUERIES:]
        print(f"Warning: {len(queries)} distinct job title(s) across your active resumes - "
              f"capping the scheduled job-cache refresh to the first {MAX_QUERIES} to keep each "
              f"run fast. Not searched by the scheduled cache (your own live searches from the "
              f"app are unaffected): {dropped}")
        queries = queries[:MAX_QUERIES]
    return queries


def sync_search_queries(active_profiles: list[dict]) -> tuple[bool, str]:
    """active_profiles: exactly what repository.list_profiles(active_only=True) returns right
    now. Never raises - same silent-fallback contract as company_sync.sync_companies_config():
    a failure here just means the scheduled cache keeps using whatever query set it last
    successfully synced (or the bootstrap default, if this has never succeeded), not a crash of
    whatever triggered this (a resume upload or a retire click).
    """
    try:
        return _sync_search_queries(_extract_queries(active_profiles))
    except subprocess.TimeoutExpired:
        return False, "A git command took too long and was cancelled - check your internet connection."
    except Exception as e:
        return False, f"Unexpected error while syncing search_queries_config.json: {e}"


def _sync_search_queries(queries: list[str]) -> tuple[bool, str]:
    if shutil.which("git") is None:
        return False, "git isn't installed or isn't on your PATH."

    LOCAL_CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)

    if (LOCAL_CLONE_DIR / ".git").exists():
        pull = _run_git(["pull", "--ff-only"], cwd=LOCAL_CLONE_DIR)
        if pull.returncode != 0:
            return False, (
                f"Couldn't update the local queries-sync clone at {LOCAL_CLONE_DIR} - it may "
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
        json.dump(queries, f, indent=2)
        f.write("\n")

    _run_git(["add", CONFIG_REPO_PATH], cwd=LOCAL_CLONE_DIR)
    status = _run_git(["status", "--porcelain", CONFIG_REPO_PATH], cwd=LOCAL_CLONE_DIR)
    if not status.stdout.strip():
        return True, "search_queries_config.json on GitHub already matches - nothing to push."

    commit = _run_git(["commit", "-m", "Update search queries from active resumes"], cwd=LOCAL_CLONE_DIR)
    if commit.returncode != 0:
        return False, f"Couldn't commit search_queries_config.json: {commit.stderr.strip()[:300]}"

    push = _run_git(["push"], cwd=LOCAL_CLONE_DIR)
    if push.returncode != 0:
        return False, (
            f"Committed locally but couldn't push (check your GitHub push access, or pull "
            f"first if origin/main moved). git said: {push.stderr.strip()[:300]}"
        )

    plural = "y" if len(queries) == 1 else "ies"
    return True, f"Pushed {len(queries)} search quer{plural} - the next scheduled cache refresh will use these."


def load_search_queries() -> list | None:
    """Reads search_queries_config.json from the SAME checkout this script is currently running
    in (not the separate sync clone above) - this is what refresh_job_cache.py calls, and in CI
    that checkout comes fresh from `actions/checkout` on every run.

    Returns None (distinct from an empty list!) if the file doesn't exist yet - callers should
    fall back to BOOTSTRAP_DEFAULT_QUERIES in that case. An empty list is a real, different
    answer: it means the file HAS been synced and there are currently zero active resumes, which
    legitimately means nothing should be searched - not "fall back to searching for someone
    else's roles."
    """
    config_path = Path(__file__).resolve().parent / CONFIG_FILE_NAME
    if not config_path.exists():
        return None
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
