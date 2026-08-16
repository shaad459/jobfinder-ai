"""Search orchestration, shared by streamlit_app.py and api_server.py so the two frontends never
duplicate (and drift out of sync on) the actual search/scoring logic - only how results get
displayed differs between them.

Two layers here:
  - run_search_for_profile / run_search_for_profile_all_companies / run_search_captured: the
    original single-profile search flow (moved out of streamlit_app.py unchanged), which the
    Streamlit app still uses directly for its single "currently uploaded resume" flow.
  - run_search_for_profiles: NEW - fans the same single-profile flow out across several saved
    resumes at once and merges the results by job URL, so one job can carry multiple scores (e.g.
    "83% as Business Analyst, 71% as Product Manager") instead of assuming one score per job. This
    is what api_server.py's /api/search endpoint uses for the resume-library feature - see
    ARCHITECTURE.md / the resume-library design notes for why matching stays per-profile
    (independent dedup via get_scored_job_urls, independent Stage 0/-1 title filtering) rather
    than trying to score once and reuse the result across resumes.
"""
import contextlib
import io

from job_aggregator import fetch_company_jobs, get_company_jobs_from_cache
from matcher import score_all_jobs
from repository import (
    get_all_companies, get_matches, get_profile_by_id, get_scored_job_urls, save_job, save_match,
)

_JOB_DISPLAY_FIELDS = ("url", "title", "company", "location", "description", "posted_date", "source")


def run_search_for_profile(profile, profile_id, company, title_override, location, relocation_ok,
                            include_aggregators=False, skip_cache=False):
    query = title_override or (profile.get("job_titles") or ["product owner"])[0]

    # Cache-first: read whatever the shared job cache (refreshed every ~12h by
    # refresh-job-cache.yml, see job_cache_sync.py) already has for this company, instead of
    # waiting on a live Workday/JSearch/Adzuna call. Falls back to a live fetch when the cache
    # has nothing for this company at all - e.g. one you just added locally that the shared
    # cache doesn't know about yet - or when skip_cache is checked, and saves those live results
    # into the local jobs table so the next search benefits too. Note the cache is only as broad
    # as refresh_job_cache.py's own query terms (see its DEFAULT_QUERIES) - if you're searching a
    # role family it doesn't cover, check "search live" to make sure you're not missing postings.
    jobs = [] if skip_cache else get_company_jobs_from_cache(
        company, location=location, relocation_ok=relocation_ok)
    if not jobs:
        jobs = fetch_company_jobs(company, query, location=location, relocation_ok=relocation_ok,
                                   include_aggregators_for_workday=include_aggregators)
        for job in jobs:
            save_job(job)

    already_scored = get_scored_job_urls(profile_id)
    new_jobs = [j for j in jobs if j["url"] not in already_scored]

    def save_batch(batch_scored):
        for job in batch_scored:
            save_job(job)
            save_match(profile_id, job)

    if new_jobs:
        score_all_jobs(profile, new_jobs, batch_size=10, on_batch_scored=save_batch,
                        title_override=title_override)

    job_urls_this_search = {j["url"] for j in jobs}
    all_matches = get_matches(profile_id)
    return [m for m in all_matches if m["url"] in job_urls_this_search]


def run_search_for_profile_all_companies(profile, profile_id, title_override, location,
                                          relocation_ok, skip_cache=False):
    all_matches = []
    for company in get_all_companies():
        all_matches.extend(run_search_for_profile(
            profile, profile_id, company, title_override, location, relocation_ok,
            include_aggregators=True, skip_cache=skip_cache))
    return all_matches


def run_search_captured(*args, **kwargs):
    """Runs a search while capturing everything it prints, so the same diagnostics the CLI entry
    points show inline (prefilter/prescreen counts, freshness/location notes, warnings) are
    visible in a UI too, not just in the terminal running the server.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        all_companies = kwargs.pop("all_companies", False)
        if all_companies:
            result = run_search_for_profile_all_companies(*args, **kwargs)
        else:
            result = run_search_for_profile(*args, **kwargs)
    return result, buf.getvalue()


def run_search_for_profiles(profile_ids: list, companies: list = None, title_override: str = None,
                             location: str = "", relocation_ok: bool = False,
                             skip_cache: bool = False) -> dict:
    """Runs a search across EVERY given profile_id against the same set of companies, then merges
    results by job URL so each job carries a list of per-profile scores instead of one score per
    job - the core of the resume-library feature (see api_server.py's /api/search).

    companies=None means "every configured company" (get_all_companies()), same as the old
    "search all companies" checkbox, and also switches on include_aggregators the same way that
    path always did (broader JSearch/Adzuna coverage on top of each Workday feed).

    Each profile's search still runs its own independent Stage 0/-1 title filtering and its own
    get_scored_job_urls dedup (see matcher.py / repository.py) - a job irrelevant to your Business
    Analyst resume is excluded for free on that pass without affecting whether it reaches Gemini
    on your Product Manager resume's pass, and a job already scored for one resume is never
    re-sent to Gemini for THAT resume again, but is still scored fresh the first time a different
    resume searches it.

    Returns {"jobs": [...], "log": "..."} - jobs sorted by each job's best score across all
    requested profiles (descending), so the strongest match for ANY of your resumes surfaces
    first regardless of which resume produced it.
    """
    search_all_companies = companies is None
    company_list = companies if companies is not None else list(get_all_companies().keys())

    merged = {}
    logs = []

    for profile_id in profile_ids:
        profile = get_profile_by_id(profile_id)
        if not profile:
            logs.append(f"[profile {profile_id}] not found - skipped.")
            continue

        if search_all_companies:
            matches, log = run_search_captured(
                profile, profile_id, title_override, location, relocation_ok,
                skip_cache=skip_cache, all_companies=True)
            matches_by_company = {"all companies": matches}
        else:
            matches_by_company = {}
            log_parts = []
            for company in company_list:
                company_matches, company_log = run_search_captured(
                    profile, profile_id, company, title_override, location, relocation_ok,
                    include_aggregators=False, skip_cache=skip_cache)
                matches_by_company[company] = company_matches
                if company_log:
                    log_parts.append(f"-- {company} --\n{company_log}")
            log = "\n".join(log_parts)

        if log:
            logs.append(f"[{profile.get('label')}]\n{log}")

        for matches in matches_by_company.values():
            for job in matches:
                url = job["url"]
                if url not in merged:
                    merged[url] = {field: job.get(field) for field in _JOB_DISPLAY_FIELDS}
                    merged[url]["scores"] = []
                merged[url]["scores"].append({
                    "profile_id": profile_id,
                    "label": profile.get("label"),
                    "match_tier": job.get("match_tier"),
                    "match_score": job.get("match_score"),
                    "match_points": job.get("match_points"),
                    "match_gaps": job.get("match_gaps"),
                    "match_reasoning": job.get("match_reasoning"),
                    "dimension_breakdown": job.get("dimension_breakdown"),
                    "opened_at": job.get("opened_at"),
                })

    jobs = list(merged.values())
    jobs.sort(key=lambda j: max((s.get("match_score") or 0) for s in j["scores"]), reverse=True)

    return {"jobs": jobs, "log": "\n\n".join(logs)}
