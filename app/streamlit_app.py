"""JobScout AI - Streamlit UI (ROADMAP_1.md Phase 5).

Same underlying pipeline as test_matcher.py and chat_assistant.py - resume parsing, profile
extraction, company-scoped multi-source search, the three-stage matching cascade, ATS resume
generation/tailoring, PDF export - just driven by widgets instead of a CLI prompt loop or a
free-text chat loop. Nothing in the pipeline itself (database.py, matcher.py, job_aggregator.py,
resume_builder.py, resume_tailor.py, pdf_export.py) was changed to build this; this file only
calls those the same way the other two entry points already do.

Run with: streamlit run streamlit_app.py

Two things are structurally different here versus the CLI entry points, both because a Streamlit
script re-runs top-to-bottom on every widget interaction rather than looping once:

1. All state that needs to survive a rerun (the extracted profile, the last search's matches, any
   generated tailored/report PDFs) lives in st.session_state, not local variables.
2. "Open this job" can't call webbrowser.open() the way chat_assistant.py does - a server can't
   launch a browser on your screen. It's a plain link instead (opens in a new tab), with a
   separate "mark as opened" action, since there's no way to detect a link click server-side.
   This is exactly the deployment consideration chat_assistant.py's own docstring flagged in
   advance.

The print()-based diagnostics inside fetch_company_jobs()/score_all_jobs() (prefilter counts,
prescreen results, freshness/location notes, warnings) go to the terminal running `streamlit run`,
not the browser - since those are genuinely useful (this whole project's debugging leaned on them
heavily), stdout is captured during each search and shown in an expander in the UI too, rather
than being lost or requiring those functions to be rewritten to return diagnostics instead of
printing them.
"""

import html
import re
import tempfile
from pathlib import Path

import streamlit as st

from resume_parser import extract_resume_text
from profile_extractor import extract_structured_profile
from resume_builder import build_ats_resume_pdf
from resume_tailor import tailor_profile_for_job, tailored_resume_filename
from job_cache_sync import sync_job_cache
from pdf_export import export_matches_to_pdf
from resume_sync import sync_all_active_resumes
from resume_storage import save_resume_file, get_resume_file_path
from search_service import run_search_for_profiles, job_sort_key
from database import init_db
from connectors.workday_connector import parse_workday_url
from connectors.greenhouse_connector import parse_greenhouse_url
from connectors.lever_connector import parse_lever_url
from connectors.avature_connector import parse_avature_url
from connectors.oracle_connector import parse_oracle_url
from repository import (
    get_or_create_profile, get_profile_by_hash, get_profile_by_id, get_gemini_call_counts_today,
    delete_stale_jobs, mark_job_opened, get_all_companies, add_company, remove_company,
    get_all_greenhouse_companies, add_greenhouse_company, remove_greenhouse_company,
    get_all_lever_companies, add_lever_company, remove_lever_company,
    get_all_avature_companies, add_avature_company, remove_avature_company,
    get_all_oracle_companies, add_oracle_company, remove_oracle_company, get_all_company_names,
    list_profiles, set_profile_label, set_profile_active,
)

DIMENSIONS = ("role", "location", "skills", "certification", "experience", "domain")
TIER_BADGE_CLASS = {
    "Strong": "jsa-badge-strong", "Good": "jsa-badge-good", "Weak": "jsa-badge-weak",
    # "Pending" - see search_service.run_search_for_profile - is a job this run's fetch touched
    # but couldn't get a Gemini verdict for yet (its Stage 2 batch failed, typically a rate-limit
    # exhaustion), not a real tier - kept visually distinct (blue, not the red/amber/grey scale
    # the real tiers use) so it doesn't read as a verdict at a glance.
    "Pending": "jsa-badge-pending",
}
LEVEL_CHIP_CLASS = {"match": "jsa-level-match", "partial": "jsa-level-partial", "none": "jsa-level-none"}


def _esc(text) -> str:
    """Escapes text pulled from job postings / profile data before dropping it into raw HTML
    (badges, chips, titles below) - this is a local single-user app so there's no real security
    stake, but a stray '<' or '&' in a scraped job title could otherwise break the markup.
    """
    return html.escape(str(text)) if text is not None else ""


# Custom CSS: Streamlit's default look is functional but plain. This injects real card styling,
# a branded dark sidebar, color-coded tier/level badges, and tighter typography, without touching
# any of the underlying widgets or logic below - if a selector ever stops matching a future
# Streamlit version, the affected element just falls back to Streamlit's default look rather than
# breaking anything.
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
/* --- Palette: black / grey / blue, per direct instruction. Every background below is a
   neutral black-grey (no navy/indigo tint), every accent is blue, all text is white/near-white. --- */
.stApp { background: #0a0a0d !important; }

/* Streamlit's own top toolbar (hamburger menu, "Deploy" button) and the file-uploader widget
   are separate native components that DON'T inherit from .stApp's background - they render
   their own surface color. These were the two most likely reasons the page still looked
   "white" after the first pass, since neither was touched by the earlier CSS at all. */
[data-testid="stHeader"] { background: #0a0a0d !important; }
[data-testid="stToolbar"] { background: transparent !important; }
[data-testid="stFileUploaderDropzone"], [data-testid="stFileUploader"] section {
    background: #1a1a1e !important; border-color: rgba(255,255,255,0.14) !important;
}
[data-testid="stFileUploaderDropzone"] * { color: #ffffff !important; }
[data-testid="stFileUploaderDropzoneInstructions"] svg { fill: #ffffff !important; }

/* Real dark theme, pinned - not just "whatever Streamlit's picker happens to compute." Streamlit's
   own Light/Dark/System theme picker recomputes a separate text-color variable for native,
   unstyled elements (section headers, widget labels, st.caption text, radio/checkbox labels) -
   if someone clicks "Light" in that picker (or their OS is in light mode and they're on
   "System"), that variable would turn dark, which is invisible against the dark background
   we're forcing above. The .jsa-* classes below already set their own color directly (badges,
   job titles, scores) so they were never affected - this specifically patches the plain,
   un-styled text that has nothing else setting its color.
   :where(...) scopes this to the main content area (not the sidebar, which already sets its
   own light-on-dark text color above) WITHOUT adding any CSS specificity, so it can never
   accidentally out-rank the more specific .jsa-* rules further down for their own elements. */
:where([data-testid="stMain"]) :where([data-testid="stMarkdownContainer"]) *,
:where([data-testid="stMain"]) label,
:where([data-testid="stMain"]) h1,
:where([data-testid="stMain"]) h2,
:where([data-testid="stMain"]) h3,
:where([data-testid="stMain"]) p,
:where([data-testid="stMain"]) span {
    color: #ffffff !important;
}

.jsa-header { display: flex; align-items: center; gap: 12px; margin-bottom: 0; }
.jsa-header .jsa-logo { font-size: 2rem; line-height: 1; }
.jsa-header h1 {
    font-size: 2rem !important; font-weight: 800 !important;
    letter-spacing: -0.03em; margin: 0 !important; color: #ffffff;
}
.jsa-subtitle { color: #a3a3a8; font-size: 0.95rem; margin: 2px 0 6px 0; }

section[data-testid="stSidebar"] { background: linear-gradient(180deg, #19191c 0%, #0a0a0d 100%); }
section[data-testid="stSidebar"] * { color: #ffffff !important; }
section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.12); }
.jsa-sidebar-brand { font-size: 1.1rem; font-weight: 800; letter-spacing: -0.02em; margin-bottom: 2px; }
.jsa-sidebar-tag { color: #a3a3a8 !important; font-size: 0.78rem; margin-bottom: 16px; }
.jsa-model-card {
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px; padding: 10px 14px; margin-bottom: 10px;
}
.jsa-model-name { font-weight: 600; font-size: 0.85rem; color: #ffffff !important; margin-bottom: 4px; }
.jsa-model-stat { font-size: 0.8rem; color: #a3a3a8 !important; }

h2, h3 { font-weight: 700 !important; letter-spacing: -0.01em; }

div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 14px !important; border: 1px solid rgba(255,255,255,0.12) !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.4) !important; background: #1a1a1e !important;
}

/* Native widgets (text inputs, selects, expanders, checkboxes/radios) already follow the dark
   palette set in .streamlit/config.toml's [theme] block - this aligns their container chrome
   (the boxes around inputs, the expander panel, dropdown popovers) with the same grey surface
   used above, and explicitly recolors the checkbox/radio controls, which don't reliably pick
   up theme colors on their own in every browser. */
div[data-testid="stTextInput"] input, div[data-testid="stTextArea"] textarea,
[data-testid="stExpander"] details,
div[data-baseweb="select"] > div, div[data-baseweb="popover"] {
    background: #1a1a1e !important; border-color: rgba(255,255,255,0.12) !important; color: #ffffff !important;
}
ul[role="listbox"] { background: #1a1a1e !important; }
ul[role="listbox"] li { color: #ffffff !important; }
[data-testid="stCheckbox"] svg, [data-testid="stRadio"] svg { fill: #3b82f6 !important; }

.stButton button, .stDownloadButton button { border-radius: 8px !important; font-weight: 600 !important; }
button[kind="primary"] { background: #2563eb !important; border-color: #2563eb !important; color: #ffffff !important; }
button[kind="primary"]:hover { background: #1d4ed8 !important; border-color: #1d4ed8 !important; }
button[kind="secondary"] {
    background: #1a1a1e !important; border-color: rgba(255,255,255,0.16) !important; color: #ffffff !important;
}

.jsa-badge {
    display: inline-block; padding: 3px 11px; border-radius: 999px;
    font-size: 0.72rem; font-weight: 700; letter-spacing: 0.03em;
    text-transform: uppercase; vertical-align: middle;
}
.jsa-badge-strong { background: #0f2e1e; color: #4ade80; }
.jsa-badge-good   { background: #332705; color: #fbbf24; }
.jsa-badge-weak   { background: #262626; color: #a3a3a8; }
.jsa-badge-pending { background: #1e293b; color: #93c5fd; }
.jsa-score { font-weight: 700; color: #ffffff; margin-left: 6px; font-size: 0.95rem; }
.jsa-coverage {
    font-weight: 600; color: #60a5fa; margin-left: 10px; font-size: 0.8rem;
    background: #0f2942; padding: 2px 8px; border-radius: 999px;
}
.jsa-job-title { font-size: 1.05rem; font-weight: 700; color: #ffffff; margin: 8px 0 2px 0; }

.jsa-level-chip {
    display: inline-block; padding: 1px 8px; border-radius: 6px;
    font-size: 0.72rem; font-weight: 700; text-transform: capitalize; margin-right: 6px;
}
.jsa-level-match   { background: #0f2e1e; color: #4ade80; }
.jsa-level-partial { background: #332705; color: #fbbf24; }
.jsa-level-none    { background: #3d1418; color: #f87171; }
</style>
"""

# gemini_utils.call_gemini() prints this exact line ("Rate limited by Gemini - waiting {N}s
# before retry {a}/{b}...") on every retry - run_search_for_profiles() (search_service.py)
# captures stdout along with everything else during a search. This regex pulls the wait time
# back out of that captured text so the UI can show a real, run-specific rate-limit summary
# instead of just a raw log dump.
_RATE_LIMIT_WAIT_PATTERN = re.compile(r"waiting (\d+)s before retry")

# The actual search/scoring flow (cache-first read, live-fetch fallback, Gemini scoring, stdout
# capture for the on-page log) lives in search_service.py, shared with api_server.py - see that
# module's docstring. run_search_for_profiles() below is what this page calls directly.


# --- Job result card ------------------------------------------------------------------------
#
# @st.fragment makes this its own independent rerun unit: clicking "Mark as opened" or "Tailor
# resume" on ONE card only reruns that card (and shows Streamlit's brief running/dim indicator
# on just that card), instead of the whole page - which is what was happening before and made
# every other card in the list look greyed out/disabled while a single job was being tailored.
# st.rerun(scope="fragment") (rather than a plain st.rerun()) is what keeps the rerun scoped to
# just this fragment instead of escalating back up to a full-app rerun.

def _ats_coverage_pct(job: dict) -> int | None:
    """BUILD_PLAN.md item 7b's "ATS keyword-coverage score" - deliberately free: match_points
    and match_gaps are already computed during precise scoring (matcher.py stage 2), so this is
    just a ratio over data that already exists, not a new Gemini call. Returns None for a job
    that never reached precise scoring (a prescreen reject or prefilter placeholder has no
    points/gaps to compute a ratio from) rather than a misleading 0%.
    """
    points = job.get("match_points") or []
    gaps = job.get("match_gaps") or []
    total = len(points) + len(gaps)
    if total == 0:
        return None
    return round(100 * len(points) / total)


def _flatten_matches_for_export(jobs: list[dict]) -> list[dict]:
    """export_matches_to_pdf() (and the emailed digest, which reuses the same function) expects
    one flat match-shaped dict per row - unchanged, deliberately, so the PDF/email code never had
    to learn about the new per-resume "scores" list. This just re-flattens a merged multi-resume
    job back into one row per (job, resume) pair, with the resume's label folded into the title
    so it's still clear which resume that row's verdict belongs to.
    """
    flattened = []
    for job in jobs:
        for s in job.get("scores", []):
            label = s.get("label") or f"Resume {s.get('profile_id')}"
            flattened.append({
                "url": job.get("url"),
                "title": f'{job.get("title")} (as {label})',
                "company": job.get("company"),
                "location": job.get("location"),
                "posted_date": job.get("posted_date"),
                "match_tier": s.get("match_tier"),
                "match_score": s.get("match_score"),
                "match_points": s.get("match_points"),
                "match_gaps": s.get("match_gaps"),
                "dimension_breakdown": s.get("dimension_breakdown"),
            })
    return flattened


@st.fragment
def _render_job_card_multi(job):
    """One card per JOB (not per job-per-resume) - the point of the resume library. A job that
    matches several of your selected resumes shows a score pill for each; clicking a pill expands
    THAT resume's fit breakdown/points/gaps below, and "Tailor resume for this job" tailors
    whichever resume's pill is currently expanded (see tailored_paths, now keyed by
    (url, profile_id) instead of just url, since the same job can be tailored differently per
    resume).
    """
    url = job.get("url")
    scores = sorted(job.get("scores") or [], key=lambda s: -(s.get("match_score") or 0))
    if not scores:
        return
    best = scores[0]

    with st.container(border=True):
        st.markdown(f'<div class="jsa-job-title">{_esc(job.get("title"))}</div>',
                    unsafe_allow_html=True)
        posted = job.get("posted_date") or "date unknown"
        st.caption(f"{job.get('company')} · {job.get('location') or 'location not listed'} "
                   f"· {job.get('source')} · posted {posted}")

        state_key = f"expanded_profile_{url}"
        if state_key not in st.session_state:
            st.session_state[state_key] = best["profile_id"]

        pill_cols = st.columns(len(scores))
        for col, s in zip(pill_cols, scores):
            with col:
                is_expanded = s["profile_id"] == st.session_state[state_key]
                label = s.get("label") or f"Resume {s['profile_id']}"
                # match_score is None for a "Pending" card (see
                # search_service.run_search_for_profile) - shown as "—" rather than the literal
                # text "None".
                score_display = s.get("match_score") if s.get("match_score") is not None else "—"
                button_label = f"{label}: {score_display} ({s.get('match_tier')})"
                # type="primary" is the actual selection indicator now (was previously just a
                # "▸ " text prefix, easy to miss at a glance across several similar-looking
                # pills) - the flagged complaint was "I don't even know the fit check is for
                # which resume," so this needs to be visually obvious, not just textually present.
                if st.button(button_label, key=f"pill_{url}_{s['profile_id']}",
                             type="primary" if is_expanded else "secondary"):
                    st.session_state[state_key] = s["profile_id"]
                    st.rerun(scope="fragment")

        expanded = next((s for s in scores if s["profile_id"] == st.session_state[state_key]), best)
        tier = expanded.get("match_tier") or "Weak"
        badge_class = TIER_BADGE_CLASS.get(tier, "jsa-badge-weak")
        coverage = _ats_coverage_pct(expanded)
        opened_note = " · already opened" if expanded.get("opened_at") else ""
        expanded_score = expanded.get("match_score")
        score_text = expanded_score if expanded_score is not None else "—"
        st.markdown(
            f'<span class="jsa-badge {badge_class}">{_esc(tier)}</span>'
            f'<span class="jsa-score">{_esc(score_text)}</span>'
            + (f'<span class="jsa-coverage">ATS coverage: {coverage}%</span>'
               if coverage is not None else '')
            + opened_note,
            unsafe_allow_html=True,
        )
        if expanded.get("match_reasoning"):
            st.caption(f"As {expanded.get('label')}: {expanded['match_reasoning']}")

        breakdown = expanded.get("dimension_breakdown") or {}
        if breakdown:
            # Named after whichever resume's pill is currently selected above, rather than a
            # bare "Fit breakdown" - with 2-3 resumes' pills sitting right next to each other,
            # an unlabeled expander made it genuinely ambiguous which one you were looking at.
            expanded_label = expanded.get("label") or f"Resume {expanded.get('profile_id')}"
            with st.expander(f"Fit breakdown — {expanded_label}"):
                for dimension in DIMENSIONS:
                    dim = breakdown.get(dimension) or {}
                    level = dim.get("level")
                    if not level:
                        continue
                    note = dim.get("note")
                    chip_class = LEVEL_CHIP_CLASS.get(level, "jsa-level-partial")
                    st.markdown(
                        f'<span class="jsa-level-chip {chip_class}">{_esc(level)}</span>'
                        f'<strong>{_esc(dimension.capitalize())}</strong>'
                        + (f" — {_esc(note)}" if note else ""),
                        unsafe_allow_html=True,
                    )

        action_cols = st.columns(3)
        with action_cols[0]:
            if url:
                # A plain link, not webbrowser.open() - this is a server-rendered page, it can't
                # launch a browser on your screen the way the CLI assistant does. Opens in a new
                # tab so the results list stays put.
                st.markdown(f"[View posting ↗]({url})")
        with action_cols[1]:
            if url and st.button("Mark as opened", key=f"open_{url}"):
                # Marks it opened against EVERY resume that matched this job, not just the
                # currently-expanded one - once you've actually opened/applied to a posting,
                # that's true regardless of which resume happened to surface it.
                for s in scores:
                    mark_job_opened(s["profile_id"], url)
                st.rerun(scope="fragment")
        with action_cols[2]:
            # Available for Strong AND Good matches - a Good-tier match with a high score (e.g.
            # 89) is still a real, worthwhile role, not just Strong ones.
            if tier in ("Strong", "Good") and url:
                tailor_key = (url, expanded["profile_id"])
                if tailor_key in st.session_state.tailored_paths:
                    with open(st.session_state.tailored_paths[tailor_key], "rb") as f:
                        st.download_button(
                            "⬇ Tailored resume", f,
                            file_name=Path(st.session_state.tailored_paths[tailor_key]).name,
                            mime="application/pdf", key=f"dl_{url}_{expanded['profile_id']}")
                elif st.button("Tailor resume for this job",
                                key=f"tailor_{url}_{expanded['profile_id']}"):
                    with st.spinner("Tailoring (one Gemini call)..."):
                        full_profile = get_profile_by_id(expanded["profile_id"])
                        # tailor_profile_for_job() reads dimension_breakdown/gaps straight off the
                        # job dict - those live on the per-resume score here, not on the merged
                        # job, so they're grafted on for this one call rather than changing that
                        # function's contract.
                        job_for_tailoring = dict(job)
                        job_for_tailoring["dimension_breakdown"] = expanded.get("dimension_breakdown")
                        job_for_tailoring["match_gaps"] = expanded.get("match_gaps")
                        tailored_profile = tailor_profile_for_job(full_profile, job_for_tailoring)
                        original_path = get_resume_file_path(expanded["profile_id"])
                        base_name = str(original_path) if original_path else (
                            f"{expanded.get('label') or 'resume'}.pdf")
                        tailored_path = tailored_resume_filename(base_name, job.get("company") or "")
                        build_ats_resume_pdf(tailored_profile, tailored_path)
                    st.session_state.tailored_paths[tailor_key] = tailored_path
                    st.rerun(scope="fragment")


# --- Page setup ----------------------------------------------------------------------------

st.set_page_config(page_title="JobScout AI", page_icon="🧭", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
init_db()

# Session-state key the "Search as" multiselect widget below is keyed on. Kept separate from
# selected_profile_ids (the plain set the rest of the app reads/writes) because Streamlit
# persists a keyed widget's own value under this key automatically - so any PROGRAMMATIC
# change to the selection (uploading a new resume, retiring one) has to write here directly,
# before the widget renders, instead of via a `default=` argument. The old code passed
# `default=[... derived from selected_profile_ids ...]` with no explicit `key=`; since that
# default was recomputed from state the widget's own output fed straight back into, every
# interaction changed the widget's identity on the next rerun (Streamlit auto-derives one from
# the default when no key is given), which reset it to a stale default and silently discarded
# whatever the user had just picked - this is what caused "can't select a new resume after
# removing one."
RESUME_SELECT_KEY = "resume_multiselect_ids"

if "selected_profile_ids" not in st.session_state:
    # None means "not yet initialized" - the resume-library section below fills this in with
    # every active resume selected by default the first time it runs each session, so a
    # returning user's whole library is searched without an extra click. After that, it's a
    # plain set of profile_ids the user has checked/unchecked in the library.
    st.session_state.selected_profile_ids = None
    st.session_state.editing_label_id = None
    # last_matches now holds MERGED jobs (one entry per job URL, each carrying a "scores" list -
    # one score per resume that matched it) rather than one flat match-per-job - see
    # search_service.run_search_for_profiles.
    st.session_state.last_matches = []
    # Keyed by (job_url, profile_id) now, not just job_url - the same job can be tailored
    # differently per resume.
    st.session_state.tailored_paths = {}
    st.session_state.match_report_path = None
    # Tracks which profile_ids have already been auto-pushed to resume-private THIS session -
    # see the upload handler below. Without this, the "harmless and cheap after the first time"
    # re-run behavior (Streamlit re-executes this block on every rerun while the uploader still
    # holds a file) would re-run a git clone/push on every single rerun instead of just once per
    # genuinely new upload.
    st.session_state.auto_synced_resume_ids = set()

if "startup_cleanup_done" not in st.session_state:
    deleted = delete_stale_jobs(max_age_days=7)
    st.session_state.startup_cleanup_done = True
    if deleted:
        st.session_state.startup_cleanup_note = f"Cleaned up {deleted} job(s) older than 7 days."

if "job_cache_synced" not in st.session_state:
    # Pulls the shared job cache (refreshed every ~12h by refresh-job-cache.yml, see
    # job_cache_sync.py) once per app session, before any search - so a search reads
    # already-cached postings instead of waiting on a live API call. Silent on failure (e.g. no
    # internet, or the job-cache repo hasn't been set up yet): _run_search below always falls
    # back to a live fetch for any company the cache comes up empty for, so this is a pure
    # speed/rate-limit optimization, never a hard dependency.
    _cache_ok, _cache_message = sync_job_cache()
    st.session_state.job_cache_synced = True
    st.session_state.job_cache_sync_note = _cache_message if _cache_ok else None

st.markdown(
    '<div class="jsa-header"><span class="jsa-logo">🧭</span><h1>JobScout AI</h1></div>'
    '<div class="jsa-subtitle">Upload your resume, search real job postings, '
    'and see exactly how you match.</div>',
    unsafe_allow_html=True,
)

if st.session_state.get("startup_cleanup_note"):
    st.caption(st.session_state.startup_cleanup_note)

if st.session_state.get("job_cache_sync_note"):
    st.caption(f"Job cache: {st.session_state.job_cache_sync_note}")

def _render_gemini_usage(slot):
    """Draws the 'Gemini usage today' cards into `slot` (an st.empty() placeholder). Split out
    so it can be called a SECOND time right after a search finishes, not just once at startup.

    Without this, the panel only got drawn once, near the top of the script - well BEFORE the
    "Search for jobs" section further down where matcher.py's actual Gemini calls happen (both
    Stage 1 prescreen, gemini-3.5-flash-lite, and Stage 2 scoring, gemini-3.5-flash). Streamlit
    doesn't automatically re-run the whole script after a button's own handling finishes within
    that same run, so the sidebar kept showing whatever the count was BEFORE the search that was
    just clicked - a full search's worth of calls (including every flash-lite call) stayed
    invisible until some unrelated later interaction happened to trigger a fresh top-to-bottom
    rerun. That's why gemini-3.5-flash-lite could look like it had "disappeared" even though
    matcher.py always calls it before gemini-3.5-flash for the same batch of jobs - see the
    search-results refresh call to this same function below for the fix.
    """
    with slot.container():
        st.markdown("**Gemini usage today**")
        counts = get_gemini_call_counts_today()
        if not counts:
            st.caption("No calls logged today.")
        for model, status_counts in counts.items():
            stats_html = "".join(
                f'<div class="jsa-model-stat">{_esc(status)}: <strong>{c}</strong></div>'
                for status, c in status_counts.items()
            )
            st.markdown(
                f'<div class="jsa-model-card">'
                f'<div class="jsa-model-name">{_esc(model)}</div>{stats_html}</div>',
                unsafe_allow_html=True,
            )


with st.sidebar:
    st.markdown('<div class="jsa-sidebar-brand">🧭 JobScout AI</div>', unsafe_allow_html=True)
    st.markdown('<div class="jsa-sidebar-tag">Local AI job-matching assistant</div>',
                unsafe_allow_html=True)
    # A placeholder rather than a plain block of st.markdown calls, specifically so
    # _render_gemini_usage can redraw it later, after a search actually runs - see that
    # function's docstring.
    gemini_usage_slot = st.empty()
_render_gemini_usage(gemini_usage_slot)

# --- Resume upload + profile extraction -----------------------------------------------------

# Every resume you add here is saved permanently (repository.get_or_create_profile - keyed by a
# hash of the resume text, so re-uploading the same file again is instant/free) rather than
# replacing whatever you had loaded before. Check a resume's box below to include it in your
# next search; a job that matches more than one selected resume shows a score for each one (see
# _render_job_card_multi) - this is the actual point of a library instead of a single "current"
# resume: e.g. a Business Analyst-tailored resume and a Product Manager-tailored one can each
# surface roles the other would have missed, since Stage 0/-1 title filtering and Gemini scoring
# both run per-resume (see search_service.run_search_for_profiles).
st.subheader("Resume library")

uploaded_file = st.file_uploader("Add a resume (.pdf or .docx)", type=["pdf", "docx"])

if uploaded_file is not None:
    # Runs on every rerun the uploader still holds a file (Streamlit doesn't clear it on its
    # own) - harmless and cheap after the first time, since get_or_create_profile short-circuits
    # to the existing profile_id by content hash and only calls Gemini on genuinely new content.
    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    resume_text = extract_resume_text(tmp_path)
    cached = get_profile_by_hash(resume_text)
    if cached:
        new_profile_id = cached["id"]
    else:
        with st.spinner("Extracting your profile with Gemini (one-time per resume)..."):
            extracted = extract_structured_profile(resume_text)
            new_profile_id = get_or_create_profile(
                resume_text, extracted, resume_filename=uploaded_file.name)
        new_label = (extracted.get("job_titles") or ["Resume"])[0]
        st.success(f'Added "{new_label}" to your resume library.')

    # Persists the ORIGINAL bytes (not just the parsed JSON) so this resume is still fully usable
    # - for tailoring filenames and for the email-alert sync button below - in a future session,
    # not just for as long as this browser tab stays open.
    save_resume_file(new_profile_id, uploaded_file.name, uploaded_file.getvalue())
    if st.session_state.selected_profile_ids is not None:
        st.session_state.selected_profile_ids.add(new_profile_id)
        # Keep the multiselect widget's own state in sync with this programmatic change -
        # see RESUME_SELECT_KEY's comment above.
        st.session_state[RESUME_SELECT_KEY] = list(st.session_state.selected_profile_ids)

    # Auto-push your active resume library to resume-private right after an upload, so the
    # scheduled email workflow picks up whatever you just searched with WITHOUT a separate,
    # easy-to-forget trip to the "Sync my active resumes" button below. Guarded by
    # auto_synced_resume_ids so this only fires once per genuinely new profile_id per session,
    # not on every rerun the uploader still holds the file (see the comment at the top of this
    # block) - a git clone/push on every rerun would be neither harmless nor cheap.
    if new_profile_id not in st.session_state.auto_synced_resume_ids:
        resumes_to_sync = []
        for lib_p in list_profiles(active_only=True):
            lib_resume_path = get_resume_file_path(lib_p["id"])
            if lib_resume_path:
                resumes_to_sync.append({
                    "profile_id": lib_p["id"],
                    "path": str(lib_resume_path),
                    "label": lib_p.get("label"),
                })
        with st.spinner("Syncing your active resumes to your private resume repo for scheduled email alerts..."):
            auto_synced_ok, auto_sync_message = sync_all_active_resumes(resumes_to_sync)
        st.session_state.auto_synced_resume_ids.add(new_profile_id)
        if auto_synced_ok:
            st.caption(f"📧 {auto_sync_message}")
        else:
            # Never block the upload itself on this - same best-effort contract as the
            # companies/search-queries auto-sync in repository.py. Surfaced as a warning (not
            # silently swallowed) since a stale scheduled digest is easy to miss otherwise; you
            # can always retry from the "Sync my active resumes" button below.
            st.warning(f"Resume added, but couldn't auto-sync it to your scheduled email alerts: {auto_sync_message}")

library = list_profiles(active_only=True)

# Self-heal: a resume saved before label-defaulting existed (or created via the earlier web-app
# testing) can have label=NULL in the database - rather than showing a permanent "(untitled
# resume)", derive one from job_titles the first time it's rendered and persist it, so this only
# ever needs fixing once, not on every load.
for p in library:
    if not p.get("label"):
        fallback_label = (p.get("job_titles") or ["Untitled resume"])[0]
        set_profile_label(p["id"], fallback_label)
        p["label"] = fallback_label

if st.session_state.selected_profile_ids is None:
    _all_ids = {p["id"] for p in library}
    st.session_state.selected_profile_ids = _all_ids
    st.session_state.setdefault(RESUME_SELECT_KEY, list(_all_ids))

if not library:
    st.info("Upload a resume above to get started.")
else:
    # One compact multiselect instead of a checkbox-per-card - this is the whole "which resumes
    # am I searching with" control day to day; renaming/retiring/ATS-download/email-sync are all
    # occasional maintenance, tucked into the collapsed expander below instead of always-visible.
    label_lookup = {p["id"]: p.get("label") or f"Resume {p['id']}" for p in library}
    # Drop any id no longer in `library` (e.g. just retired) from the widget's own stored
    # value before rendering - Streamlit errors if a keyed multiselect's session_state value
    # contains something outside `options`.
    st.session_state[RESUME_SELECT_KEY] = [
        pid for pid in st.session_state.get(RESUME_SELECT_KEY, []) if pid in label_lookup
    ]
    selected_ids = st.multiselect(
        "Search as",
        options=[p["id"] for p in library],
        format_func=lambda pid: label_lookup.get(pid, f"Resume {pid}"),
        key=RESUME_SELECT_KEY,
        help="A job that matches more than one selected resume shows a score for each.",
    )
    st.session_state.selected_profile_ids = set(selected_ids)

    with st.expander(f"Manage resume library ({len(library)} saved)", expanded=False):
        for p in library:
            row_cols = st.columns([3.5, 1, 1])
            with row_cols[0]:
                if st.session_state.editing_label_id == p["id"]:
                    label_cols = st.columns([3, 1, 1])
                    with label_cols[0]:
                        new_label = st.text_input("Label", value=p.get("label") or "",
                                                   key=f"label_input_{p['id']}",
                                                   label_visibility="collapsed")
                    with label_cols[1]:
                        if st.button("Save", key=f"save_label_{p['id']}"):
                            set_profile_label(p["id"], new_label.strip() or p.get("label"))
                            st.session_state.editing_label_id = None
                            st.rerun()
                    with label_cols[2]:
                        if st.button("Cancel", key=f"cancel_label_{p['id']}"):
                            st.session_state.editing_label_id = None
                            st.rerun()
                else:
                    meta_bits = []
                    if p.get("job_titles"):
                        meta_bits.append(", ".join(p["job_titles"][:3]))
                    if p.get("total_years_experience") is not None:
                        meta_bits.append(f"{p['total_years_experience']} yrs")
                    if p.get("domain"):
                        meta_bits.append(p["domain"])
                    st.markdown(f"**{p.get('label')}**  \n"
                                f"<span style='font-size:0.85em;color:#888'>"
                                f"{_esc(' · '.join(meta_bits) or 'No titles extracted')}</span>",
                                unsafe_allow_html=True)
            with row_cols[1]:
                if st.button("Rename", key=f"edit_label_{p['id']}"):
                    st.session_state.editing_label_id = p["id"]
                    st.rerun()
            with row_cols[2]:
                if st.button("Retire", key=f"retire_{p['id']}",
                             help="Excludes it from search without deleting its match history"):
                    # Don't touch st.session_state[RESUME_SELECT_KEY] here - the multiselect
                    # widget using that key already rendered earlier in this same script run,
                    # and Streamlit raises StreamlitAPIException on any write to a keyed
                    # widget's session_state after it's been instantiated in that run. Not
                    # needed anyway: set_profile_active() below drops this resume out of
                    # list_profiles(active_only=True), so it won't be in label_lookup on the
                    # next rerun, and the sanitization step just above the widget already
                    # strips any id no longer in label_lookup automatically.
                    set_profile_active(p["id"], False)
                    st.session_state.selected_profile_ids.discard(p["id"])
                    st.rerun()

            with st.expander("Details / ATS resume / email alerts", expanded=False):
                st.write(p.get("summary") or "")
                st.write(f"**Current location:** {p.get('current_location') or 'not stated'}")
                skills = p.get("skills") or {}
                if skills.get("hands_on"):
                    st.write(f"**Hands-on skills:** {', '.join(skills['hands_on'])}")
                if skills.get("trained"):
                    st.write(f"**Familiar with:** {', '.join(skills['trained'])}")
                certs = p.get("certifications") or []
                if certs:
                    cert_labels = [c.get("abbreviation") or c.get("name") for c in certs]
                    st.write(f"**Certifications:** {', '.join(cert_labels)}")

                ats_resume_path = f"{p.get('label') or 'resume'} ATS.pdf"
                build_ats_resume_pdf(p, ats_resume_path)
                with open(ats_resume_path, "rb") as f:
                    st.download_button("⬇ Download ATS-optimized resume", f,
                                        file_name=ats_resume_path, mime="application/pdf",
                                        key=f"ats_dl_{p['id']}")

                # Opt-in for the scheduled email digest: the GitHub Actions workflow now loops
                # over your WHOLE active library (see run_scheduled_search.py). Uploading a new
                # resume already auto-syncs your active library (see the uploader above) - this
                # button is for the cases that don't: retiring/restoring a resume changes what
                # SHOULD be synced without triggering an upload, and it's a manual way to retry
                # if the automatic sync ever failed (network hiccup, git not configured, etc.).
                st.caption(
                    f"Your daily email digest searches all {len(library)} of your active "
                    "resume(s) below - kept in sync automatically whenever you upload a resume. "
                    "Use this button to force a re-sync (e.g. after retiring one, or to retry a "
                    "failed automatic sync)."
                )
                if st.button("Sync my active resumes for scheduled email alerts",
                              key=f"email_sync_{p['id']}"):
                    resumes_to_sync = []
                    for lib_p in library:
                        lib_resume_path = get_resume_file_path(lib_p["id"])
                        if lib_resume_path:
                            resumes_to_sync.append({
                                "profile_id": lib_p["id"],
                                "path": str(lib_resume_path),
                                "label": lib_p.get("label"),
                            })
                    with st.spinner("Pushing your active resume library to your private resume repo..."):
                        synced_ok, sync_message = sync_all_active_resumes(resumes_to_sync)
                    if synced_ok:
                        st.success(sync_message)
                    else:
                        st.error(sync_message)
            st.divider()

    st.divider()

    with st.container(border=True):
        st.subheader("Search for jobs")

        selected_resumes = [p for p in library if p["id"] in st.session_state.selected_profile_ids]
        if selected_resumes:
            st.caption("Searching as: " + ", ".join(
                p.get("label") or f"Resume {p['id']}" for p in selected_resumes))
        else:
            st.caption("No resumes selected above - check at least one to search.")

        default_location = selected_resumes[0].get("current_location") or "" if selected_resumes else ""

        col1, col2 = st.columns(2)
        with col1:
            search_all = st.checkbox(
                f"Search all {len(get_all_company_names())} configured companies "
                f"(Workday/Greenhouse/Lever + JSearch/Adzuna)")
            company_input = st.text_input("Company", disabled=search_all,
                                           placeholder="e.g. Citi, Google, Barclays")
            title_input = st.text_input(
                "Specific role to search (optional)",
                placeholder="defaults to each selected resume's own titles")
        with col2:
            location_input = st.text_input("Location", value=default_location)
            relocation_ok = st.checkbox("I'm open to relocating / any location")
            skip_cache = st.checkbox(
                "Search live instead of using the cached jobs",
                help='Searches normally read from the shared job cache (refreshed every ~12h) '
                     'for speed. Check this to force a live Workday/JSearch/Adzuna search '
                     'instead - useful if you\'re searching a role the cache wasn\'t refreshed '
                     'for, or want the absolute latest postings right now.',
            )

        search_clicked = st.button("Search", type="primary", disabled=not selected_resumes)

        if search_clicked:
            if not search_all and not company_input.strip():
                st.warning("Enter a company, or check 'search all configured companies'.")
            else:
                # Live, batch-by-batch progress WHILE the search runs (see
                # search_service.run_search_for_profiles' on_progress/on_log_line), instead of
                # one opaque spinner that reveals nothing until the whole multi-company search
                # finishes - or, on a bad rate-limit run, finishes with nothing to show at all.
                # Deliberately lightweight (plain text lines, no interactive buttons/session
                # state) since it gets redrawn on every batch; the full interactive card view
                # further down still takes over once the search actually completes.
                live_status_slot = st.empty()
                live_results_slot = st.empty()
                progress = {"seen": set(), "scored": [], "rate_limit_hits": 0}

                def _on_progress(profile, company, batch_jobs):
                    for job in batch_jobs:
                        seen_key = (job.get("url"), profile["id"])
                        if seen_key in progress["seen"]:
                            continue
                        progress["seen"].add(seen_key)
                        tier = job.get("match_tier")
                        score = job.get("match_score")
                        progress["scored"].append({
                            "title": job.get("title"), "company": job.get("company"),
                            "label": profile.get("label"), "tier": tier, "score": score,
                            "is_real": tier not in (None, "Pending") and (score or 0) > 0,
                            "is_pending": tier == "Pending",
                        })

                    real_so_far = [s for s in progress["scored"] if s["is_real"]]
                    pending_so_far = [s for s in progress["scored"] if s["is_pending"]]
                    with live_results_slot.container():
                        st.caption(
                            f"{len(real_so_far)} match(es) found so far"
                            + (f" · {len(pending_so_far)} still waiting on a Gemini verdict"
                               if pending_so_far else "")
                        )
                        for s in sorted(real_so_far, key=lambda s: -(s["score"] or 0))[:15]:
                            st.markdown(
                                f'<div class="jsa-model-stat">{_esc(s["title"])} · '
                                f'{_esc(s["company"])} · as {_esc(s["label"])}: '
                                f'<strong>{_esc(s["score"])} ({_esc(s["tier"])})</strong></div>',
                                unsafe_allow_html=True,
                            )

                def _on_log_line(line):
                    if _RATE_LIMIT_WAIT_PATTERN.search(line):
                        progress["rate_limit_hits"] += 1
                        real_so_far = len([s for s in progress["scored"] if s["is_real"]])
                        live_status_slot.warning(
                            f"⏳ Hit Gemini's rate limit ({progress['rate_limit_hits']}x so far) "
                            f"- {real_so_far} match(es) found so far, continuing automatically..."
                        )

                with st.spinner(
                    "Searching and scoring across your selected resumes - this can take a while "
                    "for a fresh search, especially for a full company sweep..."
                ):
                    result = run_search_for_profiles(
                        profile_ids=list(st.session_state.selected_profile_ids),
                        companies=None if search_all else [company_input.strip()],
                        title_override=title_input.strip() or None,
                        location=location_input.strip(),
                        relocation_ok=relocation_ok,
                        skip_cache=skip_cache,
                        on_progress=_on_progress,
                        on_log_line=_on_log_line,
                    )

                live_status_slot.empty()
                live_results_slot.empty()

                st.session_state.last_matches = result["jobs"]
                log = result["log"]
                st.session_state.tailored_paths = {}
                st.session_state.match_report_path = None

                # Redraw the sidebar panel now that this search's own Gemini calls have actually
                # happened - see _render_gemini_usage's docstring for why it wouldn't otherwise
                # reflect them until some later, unrelated rerun.
                _render_gemini_usage(gemini_usage_slot)

                rate_limit_waits = [int(m) for m in _RATE_LIMIT_WAIT_PATTERN.findall(log)]
                if rate_limit_waits:
                    st.warning(
                        f"Hit Gemini's rate limit {len(rate_limit_waits)} time(s) during this "
                        f"search and automatically retried, waiting up to "
                        f"{max(rate_limit_waits)}s each time (total ~{sum(rate_limit_waits)}s "
                        f"spent waiting). See the search log below for exactly which batch. "
                        f"If this happens often, try searching fewer companies or resumes at once."
                    )
                if log.strip():
                    with st.expander("Search log", expanded=False):
                        st.code(log, language=None)

    def _add_company_and_search(name: str):
        """Shared post-add flow for any connector type (Workday/Greenhouse/Lever) - immediately
        searches the company just added, rather than only registering it for "search all
        configured companies" later. It isn't in the shared job cache yet (refresh-job-cache.yml
        only knows about companies that existed the last time it ran) - run_search_for_profiles'
        cache-miss fallback handles that automatically: an empty cache read triggers a live
        direct-connector fetch, whose results get saved to the local cache too. Location is
        deliberately left unfiltered here (unlike a normal search) since your selected resumes
        may have different locations and this is meant to be a quick "does anything show up at
        all" check, not a filtered result - run a normal search afterward for proper location
        filtering. Factored out of the Workday-only add-company form so Greenhouse's and Lever's
        forms below can share it instead of tripling this logic.
        """
        with st.spinner(f'Searching "{name}" for the first time...'):
            added_result = run_search_for_profiles(
                profile_ids=list(st.session_state.selected_profile_ids),
                companies=[name], title_override=None, location="", relocation_ok=True,
            )

        added_jobs = added_result["jobs"]
        real_added = [j for j in added_jobs
                      if any((s.get("match_score") or 0) > 0 for s in j["scores"])]
        # Stashed in session_state rather than shown directly here - a st.rerun() right after
        # rendering something usually fires before the browser gets a chance to paint it, so this
        # is picked up and shown once, right after the rerun, by the block just below "Manage
        # companies" instead (same pattern as startup_cleanup_note/job_cache_sync_note above).
        st.session_state.new_company_search_note = (
            f'Added "{name}" and searched it: {len(real_added)} match(es) found across your '
            f'selected resumes ({len(added_jobs) - len(real_added)} excluded by the '
            f'pre-filter/prescreen). It will also be included in future "search all configured '
            f'companies" runs.'
        )
        st.session_state.new_company_search_log = added_result["log"]
        # Merge by URL rather than plain concatenation, in case a job from this company was
        # already present in last_matches from a broader earlier search.
        merged = {j["url"]: j for j in (st.session_state.last_matches or [])}
        for j in added_jobs:
            merged[j["url"]] = j
        st.session_state.last_matches = list(merged.values())

    with st.expander("Manage companies"):
        st.caption('Companies searched by "search all configured companies," across five '
                   "direct-API connector types below - Workday, Greenhouse, and Lever. Paste a "
                   "company's careers/job-board URL in the matching tab to add one; no need to "
                   "know that ATS's internal naming. Avature and Oracle Cloud Recruiting are "
                   "newer, less-proven connectors than the other three - Avature has no "
                   "location or true posted-date data, and Oracle Cloud Recruiting's field names "
                   "are unverified against a live response, so double-check what comes back from "
                   "either before relying on it.")

        workday_companies = get_all_companies()
        greenhouse_companies = get_all_greenhouse_companies()
        lever_companies = get_all_lever_companies()
        avature_companies = get_all_avature_companies()
        oracle_companies = get_all_oracle_companies()
        all_rows = (
            [(name, "Workday") for name in workday_companies]
            + [(name, "Greenhouse") for name in greenhouse_companies]
            + [(name, "Lever") for name in lever_companies]
            + [(name, "Avature") for name in avature_companies]
            + [(name, "Oracle Cloud") for name in oracle_companies]
        )
        for name, source in sorted(all_rows):
            row_cols = st.columns([3, 1, 1])
            with row_cols[0]:
                st.write(name)
            with row_cols[1]:
                st.caption(source)
            with row_cols[2]:
                if st.button("Remove", key=f"remove_company_{source}_{name}"):
                    if source == "Workday":
                        remove_company(name)
                    elif source == "Greenhouse":
                        remove_greenhouse_company(name)
                    elif source == "Lever":
                        remove_lever_company(name)
                    elif source == "Avature":
                        remove_avature_company(name)
                    else:
                        remove_oracle_company(name)
                    st.rerun()

        st.divider()
        workday_tab, greenhouse_tab, lever_tab, avature_tab, oracle_tab = st.tabs(
            ["Workday", "Greenhouse", "Lever", "Avature", "Oracle Cloud"])

        with workday_tab:
            with st.form("add_workday_company_form", clear_on_submit=True):
                wd_name = st.text_input("Display name", placeholder="e.g. Tesla", key="wd_name")
                wd_url = st.text_input(
                    "Workday careers URL",
                    placeholder="https://tesla.wd1.myworkdayjobs.com/TeslaCareers", key="wd_url")
                add_workday_submitted = st.form_submit_button("Add company")

            if add_workday_submitted:
                if not wd_name.strip():
                    st.error("Enter a display name for the company.")
                elif not wd_url.strip():
                    st.error("Paste the company's Workday careers URL.")
                elif not st.session_state.selected_profile_ids:
                    st.error("Select at least one resume in the library above first.")
                else:
                    try:
                        parsed = parse_workday_url(wd_url)
                        add_company(wd_name.strip(), parsed["company"], parsed["datacenter"],
                                    parsed["site"])
                        _add_company_and_search(wd_name.strip())
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

        with greenhouse_tab:
            with st.form("add_greenhouse_company_form", clear_on_submit=True):
                gh_name = st.text_input("Display name", placeholder="e.g. Stripe", key="gh_name")
                gh_url = st.text_input(
                    "Greenhouse job board URL",
                    placeholder="https://boards.greenhouse.io/stripe", key="gh_url")
                add_greenhouse_submitted = st.form_submit_button("Add company")

            if add_greenhouse_submitted:
                if not gh_name.strip():
                    st.error("Enter a display name for the company.")
                elif not gh_url.strip():
                    st.error("Paste the company's Greenhouse job board URL.")
                elif not st.session_state.selected_profile_ids:
                    st.error("Select at least one resume in the library above first.")
                else:
                    try:
                        parsed = parse_greenhouse_url(gh_url)
                        add_greenhouse_company(gh_name.strip(), parsed["board_token"])
                        _add_company_and_search(gh_name.strip())
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

        with lever_tab:
            with st.form("add_lever_company_form", clear_on_submit=True):
                lv_name = st.text_input("Display name", placeholder="e.g. Ramp", key="lv_name")
                lv_url = st.text_input(
                    "Lever job board URL",
                    placeholder="https://jobs.lever.co/ramp", key="lv_url")
                add_lever_submitted = st.form_submit_button("Add company")

            if add_lever_submitted:
                if not lv_name.strip():
                    st.error("Enter a display name for the company.")
                elif not lv_url.strip():
                    st.error("Paste the company's Lever job board URL.")
                elif not st.session_state.selected_profile_ids:
                    st.error("Select at least one resume in the library above first.")
                else:
                    try:
                        parsed = parse_lever_url(lv_url)
                        add_lever_company(lv_name.strip(), parsed["site"])
                        _add_company_and_search(lv_name.strip())
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

        with avature_tab:
            st.caption("Best-effort: scrapes rendered search results (no confirmed public API "
                       "for Avature career sites), and can't extract a job's location or true "
                       "posted date - see the connector's docstring for the full caveat. Paste a "
                       "URL that's already location-filtered on the site itself if you can, to "
                       "work around the missing location data.")
            with st.form("add_avature_company_form", clear_on_submit=True):
                av_name = st.text_input("Display name", placeholder="e.g. Deloitte USI", key="av_name")
                av_url = st.text_input(
                    "Avature careers URL",
                    placeholder="https://usijobs.deloitte.com/en_US/careersusi", key="av_url")
                add_avature_submitted = st.form_submit_button("Add company")

            if add_avature_submitted:
                if not av_name.strip():
                    st.error("Enter a display name for the company.")
                elif not av_url.strip():
                    st.error("Paste the company's Avature careers URL.")
                elif not st.session_state.selected_profile_ids:
                    st.error("Select at least one resume in the library above first.")
                else:
                    try:
                        parsed = parse_avature_url(av_url)
                        add_avature_company(av_name.strip(), parsed["careers_url"])
                        _add_company_and_search(av_name.strip())
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

        with oracle_tab:
            st.caption("Calls Oracle's own public job-requisitions API, but the field names "
                       "this connector expects are sourced from third-party references, not "
                       "official docs, and haven't been verified live - see the connector's "
                       "docstring for the full caveat.")
            with st.form("add_oracle_company_form", clear_on_submit=True):
                or_name = st.text_input("Display name", placeholder="e.g. EXL", key="or_name")
                or_url = st.text_input(
                    "Oracle Cloud Recruiting careers URL",
                    placeholder="https://<tenant>.oraclecloud.com/hcmUI/CandidateExperience/"
                                "en/sites/<siteNumber>/jobs", key="or_url")
                add_oracle_submitted = st.form_submit_button("Add company")

            if add_oracle_submitted:
                if not or_name.strip():
                    st.error("Enter a display name for the company.")
                elif not or_url.strip():
                    st.error("Paste the company's Oracle Cloud Recruiting careers URL.")
                elif not st.session_state.selected_profile_ids:
                    st.error("Select at least one resume in the library above first.")
                else:
                    try:
                        parsed = parse_oracle_url(or_url)
                        add_oracle_company(or_name.strip(), parsed["base_url"], parsed["site_number"])
                        _add_company_and_search(or_name.strip())
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

    if st.session_state.get("new_company_search_note"):
        st.success(st.session_state.new_company_search_note)
        if st.session_state.get("new_company_search_log", "").strip():
            with st.expander("Search log for the new company", expanded=False):
                st.code(st.session_state.new_company_search_log, language=None)
        # Shown once - clear it so it doesn't linger on every later rerun (e.g. a normal search
        # or the PDF export button triggering a rerun long after this happened).
        st.session_state.new_company_search_note = None
        st.session_state.new_company_search_log = None

    # --- Results ---------------------------------------------------------------------------

    if st.session_state.last_matches:
        st.divider()

        def _job_best_score(job):
            # match_score == 0 on a score is not a genuine Gemini verdict - it's the placeholder
            # matcher.py writes for jobs excluded by the free title/experience pre-filter or the
            # prescreen pass (see SCREENED_OUT_PLACEHOLDER / _prefiltered_placeholder in
            # matcher.py). A job is only worth showing if AT LEAST ONE of its per-resume scores
            # is a genuine verdict.
            return max((s.get("match_score") or 0) for s in job.get("scores") or [{}])

        def _job_is_pending(job):
            # A "Pending" score (see search_service.run_search_for_profile) is NOT the same
            # thing as a prefilter/prescreen placeholder above, even though both currently use
            # match_score=0/None - a placeholder is a real, final verdict (excluded, on purpose),
            # while Pending means "we don't have a verdict yet." Without this check, Pending
            # jobs fell into the same "excluded_count" bucket as genuine rejects below and were
            # never actually shown - which was the whole reason a rate-limited search looked like
            # it found nothing, even after the fix that was supposed to surface them.
            return any(s.get("match_tier") == "Pending" for s in job.get("scores") or [])

        real_matches = [
            j for j in st.session_state.last_matches
            if _job_best_score(j) > 0 or _job_is_pending(j)
        ]
        excluded_count = len(st.session_state.last_matches) - len(real_matches)
        # job_sort_key (shared with search_service.py, and with the live in-progress view above)
        # puts a real verdict before a Pending one, live results before cache before past, and
        # score as the tiebreaker - not just raw score like this used to, which would have mixed
        # Pending cards in among real ones by coincidence of both scoring 0/None.
        sorted_matches = sorted(real_matches, key=job_sort_key)

        header_cols = st.columns([3, 1])
        with header_cols[0]:
            st.subheader(f"Results ({len(sorted_matches)})")
            if excluded_count:
                st.caption(f"{excluded_count} job(s) excluded by the pre-filter/prescreen (for "
                           f"every selected resume) before detailed scoring - not shown. See the "
                           f"search log above for why.")
        with header_cols[1]:
            # Moved up here (rather than below the results list) so it's reachable without
            # scrolling through the whole list first.
            if st.session_state.match_report_path:
                with open(st.session_state.match_report_path, "rb") as f:
                    st.download_button("⬇ Match report PDF", f, file_name="match_report.pdf",
                                        mime="application/pdf")
            elif st.button("Export to PDF"):
                flattened = _flatten_matches_for_export(st.session_state.last_matches)
                output_path = export_matches_to_pdf(flattened, "match_report.pdf")
                if output_path:
                    st.session_state.match_report_path = output_path
                    st.rerun()
                else:
                    st.info("No Strong or Good matches yet to export.")

        # A fixed-height container scrolls internally instead of letting the whole page grow
        # with the result count - the search panel and header above stay put on screen
        # regardless of how many jobs came back.
        with st.container(height=600, border=True):
            for job in sorted_matches:
                _render_job_card_multi(job)
    elif library:
        st.info("Run a search above to see results.")
