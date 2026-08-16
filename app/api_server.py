"""FastAPI backend for the JobScout AI web app (React frontend lives in ../frontend).

This is a thin HTTP layer over the exact same modules streamlit_app.py already used -
repository.py, search_service.py, matcher.py (via search_service), profile_extractor.py,
resume_parser.py, job_aggregator.py (via search_service), database.py. No matching/scoring logic
lives here; this file only translates HTTP requests into calls against that existing core and
JSON-serializes the result. That's deliberate: streamlit_app.py keeps working unmodified as a
lighter-weight alternative UI, and both frontends can never drift apart on how a match is
actually computed.

Run locally (same machine, same .env, same jobfinder.db as everything else in app/):
    uvicorn api_server:app --reload --port 8000

This is a LOCAL, single-user tool - same trust model as the rest of JobScout AI (runs on your
own machine, with your own API keys, over your own local network only). CORS is opened to
localhost dev ports only (see ALLOWED_ORIGINS below); there is no authentication layer, since
there is no scenario here where a second, untrusted user is on the other end of these requests.
Do not deploy this to a public host as-is.
"""
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from connectors.workday_connector import parse_workday_url
from database import init_db
from job_cache_sync import sync_job_cache
from profile_extractor import extract_structured_profile
from repository import (
    add_company, delete_stale_jobs, get_all_companies, get_gemini_call_counts_today,
    get_matches, get_or_create_profile, get_profile_by_hash, get_profile_by_id,
    mark_job_opened, remove_company, set_profile_active, set_profile_label, list_profiles,
)
from resume_parser import extract_resume_text
from search_service import run_search_for_profiles

init_db()

app = FastAPI(title="JobScout AI API")

ALLOWED_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",  # Vite dev server
    "http://localhost:4173", "http://127.0.0.1:4173",  # Vite preview (built frontend)
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Persists a copy of each uploaded resume's ORIGINAL bytes (not just its parsed profile JSON),
# keyed by profile_id - streamlit_app.py never needed this since it only ever holds one resume
# per browser session in a throwaway tempfile, but a resume LIBRARY needs to still have the
# actual file around later (e.g. for a future "push this saved resume to the private repo for
# email alerts" action - not built yet, see the multi-resume follow-up note in
# GITHUB_ACTIONS_SETUP.md/daily-job-alert.yml, which still only supports one resume at a time).
# Lives outside the jobfinder-ai repo tree, same reasoning as resume_sync.py's local clone dir.
RESUME_LIBRARY_DIR = Path.home() / ".jobscout_ai" / "resume_library"


@app.get("/api/health")
def health():
    return {"status": "ok"}


# --- Resume library ------------------------------------------------------------------------

@app.get("/api/resumes")
def api_list_resumes(active_only: bool = True):
    return list_profiles(active_only=active_only)


@app.get("/api/resumes/{profile_id}")
def api_get_resume(profile_id: int):
    profile = get_profile_by_id(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="No resume with that id.")
    return profile


class ResumePatch(BaseModel):
    label: Optional[str] = None
    active: Optional[bool] = None


@app.patch("/api/resumes/{profile_id}")
def api_patch_resume(profile_id: int, patch: ResumePatch):
    if not get_profile_by_id(profile_id):
        raise HTTPException(status_code=404, detail="No resume with that id.")
    if patch.label is not None:
        set_profile_label(profile_id, patch.label)
    if patch.active is not None:
        set_profile_active(profile_id, patch.active)
    return get_profile_by_id(profile_id)


@app.post("/api/resumes")
async def api_upload_resume(file: UploadFile = File(...), label: Optional[str] = None):
    """Uploads and parses a resume into the library. Re-uploading a resume you've already added
    (identical extracted text) is instant and free - it's recognized by content hash (see
    repository.get_or_create_profile) and just returns the existing profile, without a second
    Gemini extraction call or a duplicate library entry.
    """
    suffix = Path(file.filename or "resume").suffix.lower()
    if suffix not in (".pdf", ".docx"):
        raise HTTPException(status_code=400, detail="Only .pdf and .docx resumes are supported.")

    contents = await file.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        resume_text = extract_resume_text(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read this file: {e}")

    cached = get_profile_by_hash(resume_text)
    if cached:
        profile_id = cached["id"]
        is_new = False
    else:
        profile = extract_structured_profile(resume_text)
        profile_id = get_or_create_profile(
            resume_text, profile, label=label, resume_filename=file.filename)
        is_new = True

    RESUME_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    (RESUME_LIBRARY_DIR / f"{profile_id}{suffix}").write_bytes(contents)

    return {"profile": get_profile_by_id(profile_id), "newly_parsed": is_new}


# --- Companies -------------------------------------------------------------------------------

@app.get("/api/companies")
def api_list_companies():
    return get_all_companies()


class CompanyCreate(BaseModel):
    name: str
    workday_url: str


@app.post("/api/companies")
def api_add_company(body: CompanyCreate):
    try:
        parsed = parse_workday_url(body.workday_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Couldn't parse that Workday URL: {e}")
    add_company(body.name.strip(), parsed["company"], parsed["datacenter"], parsed["site"])
    return get_all_companies()


@app.delete("/api/companies/{name}")
def api_remove_company(name: str):
    remove_company(name)
    return get_all_companies()


# --- Search ------------------------------------------------------------------------------------

class SearchRequest(BaseModel):
    profile_ids: list[int]
    companies: Optional[list[str]] = None  # None = every configured company
    title_override: Optional[str] = None
    location: str = ""
    relocation_ok: bool = False
    skip_cache: bool = False


@app.post("/api/search")
def api_search(body: SearchRequest):
    if not body.profile_ids:
        raise HTTPException(status_code=400, detail="Select at least one resume to search with.")
    return run_search_for_profiles(
        profile_ids=body.profile_ids,
        companies=body.companies,
        title_override=body.title_override,
        location=body.location,
        relocation_ok=body.relocation_ok,
        skip_cache=body.skip_cache,
    )


@app.get("/api/resumes/{profile_id}/matches")
def api_get_matches(profile_id: int):
    """All previously-scored jobs for one saved resume - lets the UI show "your history" for a
    single resume without re-running a search, e.g. when switching back to a resume you already
    searched with earlier in the session.
    """
    if not get_profile_by_id(profile_id):
        raise HTTPException(status_code=404, detail="No resume with that id.")
    return get_matches(profile_id)


class MarkOpenedRequest(BaseModel):
    profile_id: int
    job_url: str


@app.post("/api/mark-opened")
def api_mark_opened(body: MarkOpenedRequest):
    mark_job_opened(body.profile_id, body.job_url)
    return {"status": "ok"}


# --- Job cache / housekeeping ------------------------------------------------------------------

@app.post("/api/job-cache/sync")
def api_sync_job_cache():
    success, message = sync_job_cache()
    return {"success": success, "message": message}


@app.post("/api/housekeeping/delete-stale-jobs")
def api_delete_stale_jobs(max_age_days: int = 7):
    return {"deleted": delete_stale_jobs(max_age_days=max_age_days)}


@app.get("/api/gemini-usage")
def api_gemini_usage():
    return get_gemini_call_counts_today()
