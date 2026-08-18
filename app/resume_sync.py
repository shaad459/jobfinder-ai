"""Pushes resume(s) to the private GitHub repo the scheduled email workflow reads from - lets
the Streamlit UI's "sync my active resumes for email alerts" button (see streamlit_app.py) swap
which resume(s) daily-job-alert.yml uses, without touching GitHub by hand.

Runs plain `git` subprocess commands against a local clone kept OUTSIDE the jobfinder-ai repo
tree (under your home directory), deliberately - if it lived inside app/, it would be a nested
git repo that a stray `git add .` in the main project could accidentally sweep up or conflict
with. Uses whatever git credentials/credential helper is already configured on this machine (the
same ones you used to `git push` jobfinder-ai and resume-private by hand earlier) - this module
never handles a token or password itself, and never touches jobfinder-ai's own git repo.

PRIVATE_REPO_URL was previously hardcoded to shaad459/resume-private.git - now an optional
PRIVATE_RESUME_REPO_URL env var (same .env/GitHub-secrets pattern email_sender.py already uses)
overrides it, defaulting to the same URL as before if unset, so nothing changes for the current
setup. If you ever point this at a different private repo, remember
.github/workflows/daily-job-alert.yml's "Checkout private resume repo" step has its own
`repository: shaad459/resume-private` line - that's a separate hardcode in a different file (a
GitHub Actions workflow can't read your local .env), so it needs updating by hand to match.

Two entry points:
  - sync_resume_for_email_alerts: legacy single-file push (writes resume.<ext>, replacing
    whatever was there). Kept for any external callers, but streamlit_app.py no longer calls it.
  - sync_all_active_resumes: NEW - pushes every resume you pass it as its own
    resume_<profile_id>.<ext> file, and removes any previously-pushed resume_<id>.<ext> file
    that isn't in the given list (so retiring/deleting a resume locally also stops it from being
    searched by the scheduled workflow, on the next sync). run_scheduled_search.py discovers
    every resume_*.pdf/resume_*.docx file under the checked-out private repo and searches ALL of
    them, rather than assuming exactly one resume.<ext> file - see that script's docstring.
"""
import os
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PRIVATE_REPO_URL = os.environ.get("PRIVATE_RESUME_REPO_URL") or "https://github.com/shaad459/resume-private.git"
LOCAL_CLONE_DIR = Path.home() / ".jobscout_ai" / "resume-private"

# Must match RESUME_EXT in .github/workflows/daily-job-alert.yml - if you change one, change
# the other (sync_resume_for_email_alerts warns you if they ever drift apart). Only relevant to
# the legacy single-file path; sync_all_active_resumes doesn't use a fixed extension.
CONFIGURED_RESUME_EXT = "pdf"


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=60,
    )


def sync_resume_for_email_alerts(resume_path: str) -> tuple[bool, str]:
    """Copies resume_path into the local clone of resume-private, commits, and pushes.

    Returns (success, message) - message is a short, human-readable status meant to be shown
    directly via st.success/st.error in the Streamlit UI. Never raises: every failure path
    (git missing, repo not created yet, push rejected, network error, git hanging on a
    credential prompt and hitting the subprocess timeout) is caught and turned into a message
    instead, since this is called from inside a button click, not a script that's safe to let
    crash the whole app.
    """
    try:
        return _sync_resume_for_email_alerts(resume_path)
    except subprocess.TimeoutExpired:
        return False, (
            "A git command took too long and was cancelled - this usually means git was "
            "waiting on a credential prompt it couldn't show. Try running `git push` by hand "
            f"once inside {LOCAL_CLONE_DIR} to set up credentials, then try this button again."
        )
    except Exception as e:
        return False, f"Unexpected error while syncing the resume: {e}"


def _sync_resume_for_email_alerts(resume_path: str) -> tuple[bool, str]:
    resume_file = Path(resume_path)
    if not resume_file.exists():
        return False, f"Could not find {resume_path} to sync - try uploading the resume again."

    ext = resume_file.suffix.lstrip(".").lower()
    if ext not in ("pdf", "docx"):
        return False, f"Unsupported resume type .{ext} - only .pdf and .docx are supported."

    if shutil.which("git") is None:
        return False, "git isn't installed or isn't on your PATH - install Git for Windows and try again."

    LOCAL_CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)

    if (LOCAL_CLONE_DIR / ".git").exists():
        pull = _run_git(["pull", "--ff-only"], cwd=LOCAL_CLONE_DIR)
        if pull.returncode != 0:
            return False, (
                f"Couldn't update the local resume-private clone at {LOCAL_CLONE_DIR} - it may "
                f"have local changes that don't match GitHub. git said: {pull.stderr.strip()[:300]}"
            )
    else:
        # Wipe anything half-finished from a previous failed attempt before cloning fresh -
        # `git clone` refuses to clone into a non-empty directory.
        if LOCAL_CLONE_DIR.exists():
            shutil.rmtree(LOCAL_CLONE_DIR)
        clone = _run_git(["clone", PRIVATE_REPO_URL, str(LOCAL_CLONE_DIR)], cwd=LOCAL_CLONE_DIR.parent)
        if clone.returncode != 0:
            return False, (
                f"Couldn't clone resume-private (have you created it on GitHub yet, and pushed "
                f"to it at least once? See GITHUB_ACTIONS_SETUP.md step 2). "
                f"git said: {clone.stderr.strip()[:300]}"
            )

    # Remove a stale resume of the OTHER extension (e.g. an old resume.docx left over from
    # before you switched to uploading a .pdf) so exactly one resume file exists in the repo.
    for stale_ext in ("pdf", "docx"):
        if stale_ext != ext:
            stale = LOCAL_CLONE_DIR / f"resume.{stale_ext}"
            if stale.exists():
                stale.unlink()

    dest = LOCAL_CLONE_DIR / f"resume.{ext}"
    shutil.copyfile(resume_file, dest)

    _run_git(["add", "-A"], cwd=LOCAL_CLONE_DIR)
    status = _run_git(["status", "--porcelain"], cwd=LOCAL_CLONE_DIR)
    if not status.stdout.strip():
        return True, "This is already the resume your scheduled email alerts use - nothing to update."

    commit = _run_git(["commit", "-m", "Update resume for email alerts"], cwd=LOCAL_CLONE_DIR)
    if commit.returncode != 0:
        return False, f"Couldn't commit the updated resume: {commit.stderr.strip()[:300]}"

    push = _run_git(["push"], cwd=LOCAL_CLONE_DIR)
    if push.returncode != 0:
        return False, (
            f"Committed locally but couldn't push (check your GitHub push access to "
            f"resume-private). git said: {push.stderr.strip()[:300]}"
        )

    ext_note = ""
    if ext != CONFIGURED_RESUME_EXT:
        ext_note = (
            f" Note: this is a .{ext} file, but daily-job-alert.yml's RESUME_EXT is currently "
            f"set to '{CONFIGURED_RESUME_EXT}' - update that line (and CONFIGURED_RESUME_EXT "
            f"here) and push it, or the scheduled workflow will try to parse this as the wrong "
            f"file type."
        )

    # This success message previously implied the WHOLE scheduled-email pipeline was now ready,
    # which isn't something this function can actually confirm - it only pushes a file to a git
    # repo. Whether daily-job-alert.yml can actually send anything also depends on the Gmail app
    # password and repo secrets from GITHUB_ACTIONS_SETUP.md steps 1 and 3, which this function
    # has no way to check (GitHub doesn't expose secret values for verification, by design). The
    # caveat below is appended unconditionally rather than only when something looks unfinished,
    # since there's no reliable signal here to tell the two cases apart - better to always say
    # what this did and did not confirm than to risk a false "all set" on a run that's still
    # missing a step elsewhere.
    return True, (
        "Pushed to resume-private - the next scheduled search (and any manual 'Run workflow' "
        f"trigger) will use this resume from now on, replacing the previous one.{ext_note} "
        "Note: this only confirms the resume file itself synced - it does NOT confirm your "
        "Gmail app password or GitHub Actions secrets are set up (GITHUB_ACTIONS_SETUP.md steps "
        "1 and 3). If those aren't done yet, the workflow will still fail to actually send an "
        "email even though this succeeded - check the Actions tab's run logs to confirm."
    )


def _ensure_local_clone() -> tuple[bool, str]:
    """Shared by sync_all_active_resumes: makes sure LOCAL_CLONE_DIR exists and is up to date
    with resume-private, cloning fresh if it's never been cloned on this machine before. Returns
    (True, "") on success, (False, message) on any failure - same never-raises contract as the
    rest of this module.
    """
    if shutil.which("git") is None:
        return False, "git isn't installed or isn't on your PATH - install Git for Windows and try again."

    LOCAL_CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)

    if (LOCAL_CLONE_DIR / ".git").exists():
        pull = _run_git(["pull", "--ff-only"], cwd=LOCAL_CLONE_DIR)
        if pull.returncode != 0:
            return False, (
                f"Couldn't update the local resume-private clone at {LOCAL_CLONE_DIR} - it may "
                f"have local changes that don't match GitHub. git said: {pull.stderr.strip()[:300]}"
            )
    else:
        if LOCAL_CLONE_DIR.exists():
            shutil.rmtree(LOCAL_CLONE_DIR)
        clone = _run_git(["clone", PRIVATE_REPO_URL, str(LOCAL_CLONE_DIR)], cwd=LOCAL_CLONE_DIR.parent)
        if clone.returncode != 0:
            return False, (
                f"Couldn't clone resume-private (have you created it on GitHub yet, and pushed "
                f"to it at least once? See GITHUB_ACTIONS_SETUP.md step 2). "
                f"git said: {clone.stderr.strip()[:300]}"
            )
    return True, ""


def sync_all_active_resumes(resumes: list[dict]) -> tuple[bool, str]:
    """Pushes EVERY given resume to resume-private as its own resume_<profile_id>.<ext> file, and
    deletes any previously-pushed resume_<id>.<ext> file that isn't in this list - so a resume you
    retired or deleted locally also stops being searched by the scheduled workflow, on the next
    sync, rather than lingering in the private repo forever.

    `resumes` is a list of {"profile_id": int, "path": str, "label": str} - `path` is the
    original PDF/DOCX file (from resume_storage.get_resume_file_path), `label` is only used for
    the returned summary message. Call with your WHOLE active library (e.g. streamlit_app.py's
    `library = list_profiles(active_only=True)`, each resolved to its file via
    resume_storage.get_resume_file_path) so the private repo always mirrors "every resume
    currently active", not just whichever one you happened to click sync from.

    Also removes the legacy single resume.<ext> file if present, since run_scheduled_search.py
    now searches every resume_*.<ext> file it finds instead of a single hardcoded RESUME_PATH -
    leaving the old file around would just mean it gets searched too, under a profile the
    scheduled run re-extracts from scratch (harmless, but redundant Gemini calls you don't want).

    Returns (success, message), never raises - same contract as sync_resume_for_email_alerts.
    """
    try:
        return _sync_all_active_resumes(resumes)
    except subprocess.TimeoutExpired:
        return False, (
            "A git command took too long and was cancelled - this usually means git was "
            "waiting on a credential prompt it couldn't show. Try running `git push` by hand "
            f"once inside {LOCAL_CLONE_DIR} to set up credentials, then try this button again."
        )
    except Exception as e:
        return False, f"Unexpected error while syncing your resume library: {e}"


def _sync_all_active_resumes(resumes: list[dict]) -> tuple[bool, str]:
    if not resumes:
        return False, "No active resumes to sync - add one to your library first."

    resolved = []
    skipped = []
    for r in resumes:
        path = Path(r["path"]) if r.get("path") else None
        if not path or not path.exists():
            skipped.append(r.get("label") or f"profile {r.get('profile_id')}")
            continue
        ext = path.suffix.lstrip(".").lower()
        if ext not in ("pdf", "docx"):
            skipped.append(r.get("label") or f"profile {r.get('profile_id')}")
            continue
        resolved.append((r["profile_id"], path, ext, r.get("label")))

    if not resolved:
        return False, (
            "Couldn't find usable resume files for any active resume - try re-uploading them. "
            f"Skipped: {', '.join(skipped) or 'all'}."
        )

    ok, message = _ensure_local_clone()
    if not ok:
        return False, message

    # Legacy single-file scheme - superseded by resume_<id>.<ext> below.
    for stale_ext in ("pdf", "docx"):
        stale = LOCAL_CLONE_DIR / f"resume.{stale_ext}"
        if stale.exists():
            stale.unlink()

    desired_filenames = {f"resume_{profile_id}.{ext}" for profile_id, _, ext, _ in resolved}
    for existing in LOCAL_CLONE_DIR.glob("resume_*.pdf"):
        if existing.name not in desired_filenames:
            existing.unlink()
    for existing in LOCAL_CLONE_DIR.glob("resume_*.docx"):
        if existing.name not in desired_filenames:
            existing.unlink()

    for profile_id, path, ext, _label in resolved:
        shutil.copyfile(path, LOCAL_CLONE_DIR / f"resume_{profile_id}.{ext}")

    _run_git(["add", "-A"], cwd=LOCAL_CLONE_DIR)
    status = _run_git(["status", "--porcelain"], cwd=LOCAL_CLONE_DIR)
    if not status.stdout.strip():
        return True, "Your scheduled email alerts already searches with exactly this set of resumes - nothing to update."

    commit = _run_git(["commit", "-m", "Sync active resume library for email alerts"], cwd=LOCAL_CLONE_DIR)
    if commit.returncode != 0:
        return False, f"Couldn't commit the updated resumes: {commit.stderr.strip()[:300]}"

    push = _run_git(["push"], cwd=LOCAL_CLONE_DIR)
    if push.returncode != 0:
        return False, (
            f"Committed locally but couldn't push (check your GitHub push access to "
            f"resume-private). git said: {push.stderr.strip()[:300]}"
        )

    skipped_note = f" Skipped (no usable file): {', '.join(skipped)}." if skipped else ""
    labels = ", ".join(label or f"profile {pid}" for pid, _, _, label in resolved)
    return True, (
        f"Pushed {len(resolved)} active resume(s) to resume-private ({labels}) - the next "
        f"scheduled search (and any manual 'Run workflow' trigger) will search with all of "
        f"them.{skipped_note} Note: this only confirms the resume files themselves synced - it "
        "does NOT confirm your Gmail app password or GitHub Actions secrets are set up "
        "(GITHUB_ACTIONS_SETUP.md steps 1 and 3). If those aren't done yet, the workflow will "
        "still fail to actually send an email even though this succeeded - check the Actions "
        "tab's run logs to confirm."
    )
