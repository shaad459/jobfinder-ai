"""Persists the ORIGINAL bytes of every resume in the library (not just its parsed profile
JSON), keyed by profile_id - shared by streamlit_app.py and api_server.py so both can save/read
from the same place instead of each keeping its own throwaway copy.

Why this needs to exist at all: repository.py's `profiles` table only stores the Gemini-extracted
structured JSON, which is all matching/scoring ever needs - but a few OTHER things genuinely need
the original file back: building a filename for a tailored resume PDF, and pushing a resume to
the private repo for scheduled email alerts (resume_sync.py needs real PDF/DOCX bytes, not JSON).
Previously (single-resume-per-session Streamlit flow) this was solved with a throwaway tempfile
that only lived as long as the browser tab; a resume LIBRARY needs it to survive across sessions,
so a resume you uploaded last week is still fully usable today without re-uploading.

Lives outside the jobfinder-ai repo tree, same reasoning as resume_sync.py's local clone dir -
this is user data, not something that belongs in git history.
"""
from pathlib import Path

RESUME_LIBRARY_DIR = Path.home() / ".jobscout_ai" / "resume_library"


def save_resume_file(profile_id: int, filename: str, contents: bytes) -> Path:
    RESUME_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename or "resume.pdf").suffix.lower() or ".pdf"
    path = RESUME_LIBRARY_DIR / f"{profile_id}{suffix}"
    path.write_bytes(contents)
    return path


def get_resume_file_path(profile_id: int) -> Path | None:
    """Returns the saved original file for this profile_id, or None if it was never saved here
    (e.g. a profile row created before this module existed). Callers that need the original file
    - ATS filename generation, email-alert sync - should handle None gracefully rather than
    assume it's always present.
    """
    for ext in (".pdf", ".docx"):
        path = RESUME_LIBRARY_DIR / f"{profile_id}{ext}"
        if path.exists():
            return path
    return None
