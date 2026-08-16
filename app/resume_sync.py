"""Pushes the currently-loaded resume to the private GitHub repo the scheduled email workflow
reads from - lets the Streamlit UI's "use this resume for my email alerts" button (see
streamlit_app.py) swap which resume daily-job-alert.yml uses, without touching GitHub by hand.

Runs plain `git` subprocess commands against a local clone kept OUTSIDE the jobfinder-ai repo
tree (under your home directory), deliberately - if it lived inside app/, it would be a nested
git repo that a stray `git add .` in the main project could accidentally sweep up or conflict
with. Uses whatever git credentials/credential helper is already configured on this machine (the
same ones you used to `git push` jobfinder-ai and resume-private by hand earlier) - this module
never handles a token or password itself, and never touches jobfinder-ai's own git repo.
"""
import shutil
import subprocess
from pathlib import Path

PRIVATE_REPO_URL = "https://github.com/shaad459/resume-private.git"
LOCAL_CLONE_DIR = Path.home() / ".jobscout_ai" / "resume-private"

# Must match RESUME_EXT in .github/workflows/daily-job-alert.yml - if you change one, change
# the other (sync_resume_for_email_alerts warns you if they ever drift apart).
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

    return True, (
        "Pushed to resume-private - tomorrow's scheduled search (and any manual 'Run workflow' "
        f"trigger) will use this resume from now on, replacing the previous one.{ext_note}"
    )
